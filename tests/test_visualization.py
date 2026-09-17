import asyncio
import time

import httpx
import pytest

from agentic_simulation.config import (
    AgentsConfig,
    AppConfig,
    ResourceConfig,
    ResourcesConfig,
    VisualizationConfig,
    WorldConfig,
)
from agentic_simulation.simulation import SimulationBuilder, SimulationEngine
from agentic_simulation.simulation.engine import TickReport
from agentic_simulation.visualization import create_app
from agentic_simulation.visualization.server import SimulationController


def test_visualization_and_controls() -> None:
    config = AppConfig(
        world=WorldConfig(
            width=12,
            height=10,
            resources=ResourcesConfig(
                food=ResourceConfig(initial_nodes=3, capacity=10),
                water=ResourceConfig(initial_nodes=2, capacity=10),
            ),
        ),
        agents=AgentsConfig(initial_population=12),
    )

    async def exercise_api() -> None:
        app = create_app(config)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            initial = (await client.get("/api/world")).json()
            assert initial["tick"] == 0
            assert len(initial["layers"]["terrain"]) == 120
            assert len(initial["agents"]) == 12

            stepped = (await client.post("/api/control/step")).json()
            assert stepped["tick"] == 1
            assert len(stepped["agents"]) == 12

            regenerated = (
                await client.post("/api/control/regenerate", json={"seed": 99})
            ).json()
            assert regenerated["tick"] == 0
            assert regenerated != initial

    asyncio.run(exercise_api())


def control_config() -> AppConfig:
    return AppConfig(
        world=WorldConfig(width=12, height=10),
        agents=AgentsConfig(initial_population=8),
        visualization=VisualizationConfig(
            ticks_per_second=1000, broadcast_every_ticks=20
        ),
    )


async def wait_until_paused(controller: SimulationController) -> None:
    async def wait() -> None:
        while controller.running:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=5)


def test_bounded_runs_stop_exactly_and_preserve_simulation_results() -> None:
    async def exercise() -> None:
        config = control_config()
        expected = SimulationBuilder.build(config)
        controller = SimulationController(config)
        subscriber = controller.subscribe()
        try:
            # Leave the initial map queued, as if the viewer were slow.
            subscriber.put_nowait(await controller.frame())
            controller.play(3)
            await wait_until_paused(controller)
            finished = await asyncio.wait_for(subscriber.get(), 1)
            assert finished["kind"] == "world"
            assert finished["tick"] == 3  # Delivered despite a 20-tick sampling rate.
            assert finished["control"]["remaining_ticks"] == 0
            assert finished["control"]["running"] is False
            controller.set_speed(500)
            controller.play(3)
            await wait_until_paused(controller)
            for _ in range(6):
                expected.advance()
            status = await controller.status()
            assert status["tick"] == 6
            assert status["state_hash"] == expected.state_hash()
            assert status["ticks_per_second"] == 500
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_manual_step_pause_and_regeneration_reach_all_viewers() -> None:
    async def exercise() -> None:
        controller = SimulationController(control_config())
        queues = [controller.subscribe(), controller.subscribe()]
        try:
            initial = await controller.frame()
            await controller.step()
            for queue in queues:
                assert queue.get_nowait()["tick"] == 1
            controller.play(100)
            with pytest.raises(RuntimeError, match="pause"):
                await controller.step()
            with pytest.raises(RuntimeError, match="pause"):
                controller.play(5)
            controller.pause()
            for queue in queues:
                paused = queue.get_nowait()
                assert paused["control"]["running"] is False
                assert paused["control"]["target_tick"] is None
            controller.play(100)
            replacement = await controller.regenerate(73)
            assert replacement["tick"] == 0
            assert replacement["run_id"] != initial["run_id"]
            assert replacement["control"]["seed"] == 73
            assert replacement["control"]["running"] is False
            assert replacement["control"]["target_tick"] is None
            controller.set_speed(16)
            for queue in queues:
                # A later control update must preserve the replacement map.
                latest = queue.get_nowait()
                assert latest["kind"] == "world"
                assert latest["run_id"] == replacement["run_id"]
                assert latest["control"]["ticks_per_second"] == 16
            assert (await controller.frame())["run_id"] == replacement["run_id"]
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_control_api_validates_requests() -> None:
    async def exercise() -> None:
        app = create_app(control_config())
        controller = app.state.simulation_controller
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                for ticks in (0, -1, 1.5, True, 1_000_001):
                    response = await client.post(
                        "/api/control/run", json={"ticks": ticks}
                    )
                    assert response.status_code == 422
                for speed in (0, -1, 1001, "NaN", "Infinity"):
                    response = await client.post(
                        "/api/control/speed", json={"ticks_per_second": speed}
                    )
                    assert response.status_code == 422
                speed = await client.post(
                    "/api/control/speed", json={"ticks_per_second": 100}
                )
                assert speed.json()["ticks_per_second"] == 100
                response = await client.post("/api/control/run", json={"ticks": 2})
                assert response.json()["target_tick"] == 2
                await wait_until_paused(controller)
                status = (await client.get("/api/status")).json()
                assert status["tick"] == 2
                assert status["state"] == "paused"
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_slow_ticks_yield_to_pause_and_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SimulationEngine.advance

    def slow_tick(simulation: SimulationEngine) -> TickReport:
        time.sleep(0.003)  # Exceed the 1ms target interval deliberately.
        return original(simulation)

    monkeypatch.setattr(SimulationEngine, "advance", slow_tick)

    async def exercise() -> None:
        controller = SimulationController(control_config())
        try:
            controller.play(100)
            await asyncio.sleep(0.001)
            controller.pause()
            assert 0 < (await controller.status())["tick"] < 100
        finally:
            await asyncio.wait_for(controller.close(), 1)

    asyncio.run(exercise())


def test_failed_clock_reports_error_and_requires_regeneration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SimulationEngine.advance
    failed = False

    def fail_once(simulation: SimulationEngine) -> TickReport:
        nonlocal failed
        if not failed:
            failed = True
            raise ValueError("test tick failure")
        return original(simulation)

    monkeypatch.setattr(SimulationEngine, "advance", fail_once)

    async def exercise() -> None:
        controller = SimulationController(control_config())
        queue = controller.subscribe()
        try:
            controller.play()
            await wait_until_paused(controller)
            assert queue.get_nowait()["control"]["state"] == "error"
            assert (await controller.status())["error"]
            with pytest.raises(RuntimeError, match="Regenerate"):
                controller.play()
            with pytest.raises(RuntimeError, match="Regenerate"):
                await controller.step()
            reset = await controller.regenerate(42)
            assert reset["control"]["error"] is None
            controller.play(2)
            await wait_until_paused(controller)
            assert (await controller.status())["tick"] == 2
        finally:
            await controller.close()

    asyncio.run(exercise())
