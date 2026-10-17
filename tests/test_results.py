import asyncio
import io
import json
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest

from agentic_simulation.config import (
    AgentsConfig,
    AppConfig,
    LifeConfig,
    VisualizationConfig,
    WorldConfig,
)
from agentic_simulation.simulation import SimulationEngine
from agentic_simulation.visualization import create_app
from agentic_simulation.visualization.results import ExecutionRecord, ResultStore
from agentic_simulation.visualization.server import SimulationController


def config() -> AppConfig:
    return AppConfig(
        world=WorldConfig(width=12, height=10),
        agents=AgentsConfig(initial_population=5),
        visualization=VisualizationConfig(
            ticks_per_second=1000, broadcast_every_ticks=100
        ),
    )


async def finished(controller: SimulationController) -> None:
    async with asyncio.timeout(5):
        while controller.running:
            await asyncio.sleep(0.001)


def test_completion_persists_exact_endpoint_and_downloads(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = create_app(config(), results_directory=tmp_path)
        controller = app.state.simulation_controller
        queue = controller.subscribe()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post("/api/control/run", json={"ticks": 53})
                await finished(controller)
                notice = queue.get_nowait()
                result_id = notice["control"]["completed_result_id"]
                assert result_id
                assert notice["tick"] == 53
                response = await client.get(f"/api/results/{result_id}")
                assert response.status_code == 200
                result = response.json()
                summary = result["summary"]
                assert summary["status"] == "complete"
                assert summary["elapsed_ticks"] == 53
                assert summary["initial_population"] == 5
                assert summary["final_population"] == 5
                assert summary["births"] == summary["deaths"] == 0
                assert [row["tick"] for row in result["metrics"]] == [0, 50, 53]
                assert result["manifest"]["configuration"] == config().model_dump()
                assert (
                    summary["state_hash"] == (await controller.status())["state_hash"]
                )
                assert {p.name for p in (tmp_path / result_id).iterdir()} == {
                    "manifest.json",
                    "metrics.jsonl",
                    "summary.json",
                    "summary.csv",
                    "report.html",
                }
                report = await client.get(f"/api/results/{result_id}/report")
                assert report.status_code == 200
                assert "<svg" in report.text and "complete" in report.text
                download = await client.get(f"/api/results/{result_id}/download")
                assert download.headers["content-type"] == "application/zip"
                with ZipFile(io.BytesIO(download.content)) as archive:
                    assert json.loads(archive.read("summary.json")) == summary
                    assert b"<svg" in archive.read("report.html")
                saved = (tmp_path / result_id / "summary.json").read_bytes()
                await client.post("/api/control/pause")
                await client.post("/api/control/step")
                assert (await controller.status())["completed_result_id"] == result_id
                assert (tmp_path / result_id / "summary.json").read_bytes() == saved
                await client.post("/api/control/regenerate", json={"seed": 99})
                assert (await client.get(f"/api/results/{result_id}")).json() == result
                assert len((await client.get("/api/results")).json()["results"]) == 1
                assert (await client.get("/api/results/not-a-uuid")).status_code == 404
        finally:
            await controller.close()
        # A new server instance can read prior results without a live simulation.
        restarted = create_app(config(), results_directory=tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test"
        ) as client:
            assert (await client.get(f"/api/results/{result_id}")).json() == result
        await restarted.state.simulation_controller.close()

    asyncio.run(exercise())


def test_pause_and_shutdown_save_interrupted_segments(tmp_path: Path) -> None:
    async def exercise() -> None:
        controller = SimulationController(config(), tmp_path)
        controller.play(1000)
        await asyncio.sleep(0.003)
        controller.pause()
        first = (await controller.status())["latest_result"]
        assert first["status"] == "interrupted"
        assert (await controller.status())["completed_result_id"] is None
        start = first["end_tick"]
        controller.play(2)
        await finished(controller)
        second = (await controller.status())["latest_result"]
        assert second["status"] == "complete"
        assert second["start_tick"] == start
        assert second["end_tick"] == start + 2
        assert second["elapsed_ticks"] == 2
        assert first["result_id"] != second["result_id"]
        controller.play()
        await controller.close()
        third = (await controller.status())["latest_result"]
        assert third["status"] == "interrupted"
        assert "shut down" in third["reason"]
        assert len(ResultStore(tmp_path).list_results()) == 3

    asyncio.run(exercise())


def test_failed_tick_uses_last_successful_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(simulation: SimulationEngine) -> None:
        simulation.world.advance()  # Deliberately leave a partially mutated state.
        raise ValueError("test failure")

    monkeypatch.setattr(SimulationEngine, "advance", fail)

    async def exercise() -> None:
        controller = SimulationController(config(), tmp_path)
        try:
            controller.play(2)
            await finished(controller)
            status = await controller.status()
            assert status["completed_result_id"] is None
            summary = status["latest_result"]
            assert summary["status"] == "failed"
            assert summary["end_tick"] == 0
            assert summary["state_hash"] is None
            assert (
                ResultStore(tmp_path).read(summary["result_id"])["manifest"]["status"]
                == "failed"
            )
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_extinction_is_an_outcome_not_early_completion(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = AppConfig(
            world=WorldConfig(width=12, height=10),
            agents=AgentsConfig(initial_population=2),
            life=LifeConfig(max_age=2),
            visualization=VisualizationConfig(ticks_per_second=1000),
        )
        controller = SimulationController(settings, tmp_path)
        try:
            controller.play(4)
            await finished(controller)
            summary = (await controller.status())["latest_result"]
            assert summary["status"] == "complete"
            assert summary["end_tick"] == 4
            assert summary["final_population"] == 0
            assert summary["deaths"] == 2
            assert summary["extinction_tick"] == 2
            assert summary["extinct"]
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_unfinished_record_is_never_presented_as_complete(tmp_path: Path) -> None:
    store = ResultStore(tmp_path)
    summary = {
        "result_id": "00000000-0000-0000-0000-000000000001",
        "run_id": "test",
        "status": "running",
        "started_at": "2026-10-17T00:00:00Z",
        "start_tick": 0,
        "core": "random",
        "seed": 42,
        "initial_population": 5,
    }
    path = store.path(summary["result_id"])
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps(
            {
                **summary,
                "initial": summary,
                "configuration": config().model_dump(),
                "sample_every": 50,
            }
        )
    )
    (path / "metrics.jsonl").write_text(
        json.dumps({"tick": 0, "population": 5, "births": 0, "deaths": 0})
        + '\n{"tick":'
    )
    result = store.read(summary["result_id"])
    assert result["summary"]["status"] == "incomplete"
    assert len(result["metrics"]) == 1
    assert store.list_results()[0]["status"] == "incomplete"
    with ZipFile(io.BytesIO(store.archive(summary["result_id"]))) as archive:
        assert b"incomplete" in archive.read("report.html")


def test_continued_segments_do_not_recount_prior_births(tmp_path: Path) -> None:
    async def exercise() -> None:
        settings = AppConfig(
            world=WorldConfig(width=12, height=10),
            agents=AgentsConfig(
                initial_population=1, brain="genetic", initial_energy=160
            ),
            life=LifeConfig(metabolism_cost=0, reproduction_min_age=1),
            visualization=VisualizationConfig(ticks_per_second=1000),
        )
        controller = SimulationController(settings, tmp_path)
        try:
            controller.play(1)
            await finished(controller)
            first = (await controller.status())["latest_result"]
            assert first["births"] == 1
            assert first["final_population"] == 2
            controller.play(1)
            await finished(controller)
            second = (await controller.status())["latest_result"]
            assert second["initial_population"] == 2
            assert second["births"] == 0
            assert second["deaths"] == 0
            assert second["start_tick"] == 1
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_storage_failure_stops_execution_without_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ExecutionRecord._write_json

    def fail_summary(self: ExecutionRecord, name: str, value: dict) -> None:
        if name == "summary.json":
            raise OSError("disk full")
        original(self, name, value)

    monkeypatch.setattr(ExecutionRecord, "_write_json", fail_summary)

    async def exercise() -> None:
        controller = SimulationController(config(), tmp_path)
        try:
            controller.play(1)
            await finished(controller)
            status = await controller.status()
            assert status["results_error"]
            assert status["completed_result_id"] is None
            assert ResultStore(tmp_path).list_results()[0]["status"] == "incomplete"
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_unwritable_storage_rejects_start_without_replacing_world(
    tmp_path: Path,
) -> None:
    unavailable = tmp_path / "a-file-not-a-directory"
    unavailable.write_text("occupied")

    async def exercise() -> None:
        app = create_app(config(), results_directory=unavailable)
        controller = app.state.simulation_controller
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                before = (await client.get("/api/world")).json()
                for endpoint, request in (
                    ("run", {"ticks": 3}),
                    (
                        "new-experiment",
                        {
                            "expected_run_id": before["run_id"],
                            "world": before["run_configuration"]["world"],
                            "agents": before["run_configuration"]["agents"],
                            "life": before["run_configuration"]["life"],
                            "ticks": 3,
                        },
                    ),
                ):
                    response = await client.post(
                        f"/api/control/{endpoint}", json=request
                    )
                    assert response.status_code == 503
                    assert "save results" in response.json()["detail"]
                    assert (await client.get("/api/world")).json() == before
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_metric_write_error_publishes_failure_even_between_broadcasts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ExecutionRecord.observe

    def fail_sample(self: ExecutionRecord, frame: dict, *, force: bool = False) -> None:
        if not force and frame["tick"] == 1:
            raise OSError("disk full")
        original(self, frame, force=force)

    monkeypatch.setattr(ExecutionRecord, "observe", fail_sample)

    async def exercise() -> None:
        controller = SimulationController(config(), tmp_path)
        queue = controller.subscribe()
        try:
            controller.play(10)
            await finished(controller)
            notice = queue.get_nowait()["control"]
            assert notice["results_error"]
            assert not notice["running"]
            assert notice["target_tick"] is None
            assert notice["completed_result_id"] is None
            assert notice["latest_result"]["status"] == "failed"
        finally:
            await controller.close()

    asyncio.run(exercise())
