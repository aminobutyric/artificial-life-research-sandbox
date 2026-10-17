import asyncio
from copy import deepcopy

import httpx
import pytest

from agentic_simulation.actions import ActionIntent, ActionType
from agentic_simulation.actions.resolver import ActionResolver
from agentic_simulation.config import AgentsConfig, AppConfig, LifeConfig, WorldConfig
from agentic_simulation.simulation import SimulationBuilder
from agentic_simulation.visualization import create_app


def setup_config() -> AppConfig:
    return AppConfig(
        world=WorldConfig(width=12, height=10),
        agents=AgentsConfig(initial_population=4),
    )


def test_new_run_is_atomic_bounded_and_reproducible() -> None:
    async def exercise() -> None:
        app = create_app(setup_config())
        controller = app.state.simulation_controller
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                initial = (await client.get("/api/world")).json()
                assert {core["id"] for core in initial["core_catalog"]} == {
                    "random",
                    "genetic",
                }
                config = initial["run_configuration"]
                config["world"]["seed"] = 91
                config["agents"]["brain"] = "genetic"
                config["agents"]["initial_population"] = 6
                request = {
                    "expected_run_id": initial["run_id"],
                    "world": config["world"],
                    "agents": config["agents"],
                    "life": config["life"],
                    "ticks": 3,
                }
                response = await client.post(
                    "/api/control/new-experiment", json=request
                )
                assert response.status_code == 200
                started = response.json()
                assert started["tick"] == 0
                assert started["run_id"] != initial["run_id"]
                assert started["control"]["target_tick"] == 3
                assert started["control"]["horizon"] == 3
                assert started["control"]["running"]
                assert started["run_configuration"] == config
                assert {a["brain"] for a in started["agents"]} == {"genetic"}
                # A stale browser cannot replace a more recently created run.
                stale = await client.post("/api/control/new-experiment", json=request)
                assert stale.status_code == 409
                request["expected_run_id"] = started["run_id"]
                busy = await client.post("/api/control/new-experiment", json=request)
                assert busy.status_code == 409
                async with asyncio.timeout(5):
                    while controller.running:
                        await asyncio.sleep(0.001)
                expected = SimulationBuilder.build(AppConfig.model_validate(config))
                for _ in range(3):
                    expected.advance()
                status = (await client.get("/api/status")).json()
                assert status["state_hash"] == expected.state_hash()
                assert status["tick"] == 3
                frozen = (await client.get("/api/world")).json()
                await client.post("/api/control/speed", json={"ticks_per_second": 50})
                await client.post("/api/control/step")
                assert (await client.get("/api/world")).json()[
                    "run_configuration"
                ] == frozen["run_configuration"]
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_invalid_setup_preserves_active_run() -> None:
    async def exercise() -> None:
        app = create_app(setup_config())
        controller = app.state.simulation_controller
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                initial = (await client.get("/api/world")).json()
                config = initial["run_configuration"]
                valid = {
                    "expected_run_id": initial["run_id"],
                    "world": config["world"],
                    "agents": config["agents"],
                    "life": config["life"],
                    "ticks": 10,
                }
                invalid = []
                for ticks in (0, True, 1.5, 1_000_001):
                    invalid.append({**valid, "ticks": ticks})
                for section, key, value in (
                    ("agents", "brain", "memory"),
                    ("agents", "initial_population", 3000),
                    ("agents", "initial_energy", 1000),
                    ("agents", "idle_probability", "NaN"),
                    ("world", "width", 3),
                    ("life", "mutation_probability", 1.1),
                ):
                    changed = deepcopy(valid)
                    changed[section][key] = value
                    invalid.append(changed)
                changed = deepcopy(valid)
                changed["world"]["generation"]["water_level"] = 0.9
                invalid.append(changed)
                invalid.append({**valid, "unexpected": 1})
                for request in invalid:
                    response = await client.post(
                        "/api/control/new-experiment", json=request
                    )
                    assert response.status_code == 422, response.text
                    assert (await client.get("/api/world")).json() == initial
        finally:
            await controller.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("core", ["random", "genetic"])
def test_children_inherit_selected_core(core: str) -> None:
    config = AppConfig.model_validate(
        {
            "world": {"width": 12, "height": 10},
            "agents": {"initial_population": 1, "brain": core},
            "life": LifeConfig(reproduction_min_age=1).model_dump(),
        }
    )
    sim = SimulationBuilder.build(config)
    parent = sim.agents.living()[0]
    sim.agents.age_all()
    # Direct intent exercises inheritance even for the movement-only baseline.
    resolver = ActionResolver(
        sim.world, sim.agents, sim.spatial_index, config.agents, config.life
    )
    results = resolver.resolve_all((ActionIntent(parent.id, ActionType.REPRODUCE),), 1)
    events = sim.lifecycle.births_after_actions(results, 1)
    assert len(events) == 1
    assert sim.brains.name_for(events[0].agent_id) == core
    assert {sim.brains.name_for(agent.id) for agent in sim.agents.living()} == {core}
