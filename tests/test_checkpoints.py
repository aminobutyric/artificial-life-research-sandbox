import asyncio
import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import httpx
import numpy as np
import pytest

from agentic_simulation.agents import Observation
from agentic_simulation.brains import BrainSystem, DecisionContext, RandomBrain
from agentic_simulation.cli import main
from agentic_simulation.config import (
    AgentsConfig,
    AppConfig,
    LifeConfig,
    ResourceConfig,
    ResourcesConfig,
    VisualizationConfig,
    WorldConfig,
)
from agentic_simulation.simulation import (
    SimulationBuilder,
    SimulationEngine,
    checkpoint_bytes,
    load_checkpoint,
    restore_checkpoint,
    save_checkpoint,
)
from agentic_simulation.simulation.checkpoint import continuation_hash
from agentic_simulation.visualization import create_app
from agentic_simulation.world import WorldSnapshot


def settings(core: str = "genetic", *, max_age: int = 2000) -> AppConfig:
    return AppConfig.model_validate(
        {
            "world": WorldConfig(
                width=12,
                height=10,
                seed=71,
                resources=ResourcesConfig(
                    food=ResourceConfig(
                        initial_nodes=20,
                        capacity=100,
                        regeneration_rate=0.3,
                    )
                ),
            ).model_dump(),
            "agents": AgentsConfig(
                initial_population=8, initial_energy=160
            ).model_dump()
            | {"brain": core},
            "life": LifeConfig(
                metabolism_cost=0.05,
                reproduction_min_age=2,
                reproduction_cooldown=3,
                max_age=max_age,
            ).model_dump(),
            "visualization": VisualizationConfig(ticks_per_second=1000).model_dump(),
        }
    )


@pytest.mark.parametrize("core", ["random", "genetic"])
def test_full_checkpoint_continues_with_identical_events_and_hashes(
    tmp_path: Path,
    core: str,
) -> None:
    sim = SimulationBuilder.build(settings(core))
    if core == "random":
        # Prove actual core state is restored, not inferred only from AppConfig.
        sim.brains.remove(1)
        sim.brains.register(1, RandomBrain(0.95))
    # Exercise a non-default decision seed and non-initial world RNG state.
    sim._brain_system = BrainSystem(sim.brains, 918273)
    sim.world._rng.random(13)
    sim.world._resources._next_id += 17
    for _ in range(19):
        sim.advance()
    path = tmp_path / "state.alife"
    saved_hash = sim.state_hash()
    saved_continuation = continuation_hash(sim)
    save_checkpoint(sim, path)
    restored = load_checkpoint(path)
    assert restored.config == sim.config
    assert restored.tick == 19
    assert restored.decision_seed == 918273
    assert restored.state_hash() == saved_hash
    assert continuation_hash(restored) == saved_continuation
    assert restored.world.snapshot().rng_state == sim.world.snapshot().rng_state
    assert restored.agents.next_id == sim.agents.next_id
    for _ in range(35):
        assert restored.advance() == sim.advance()
        assert restored.state_hash() == sim.state_hash()
        assert continuation_hash(restored) == continuation_hash(sim)
    # The saved snapshot remains independent of later live mutation.
    assert load_checkpoint(path).state_hash() == saved_hash


def test_reused_slots_lineage_and_cooldown_survive(tmp_path: Path) -> None:
    sim = SimulationBuilder.build(settings())
    # Simulate a removed founder, then allow the lifecycle to reuse its slot.
    sim.agents.remove(2)
    sim.spatial_index.remove(2)
    sim.brains.remove(2)
    sim.lifecycle.deaths += 1
    for _ in range(6):
        sim.advance()
    assert sim.lifecycle.births > 0
    assert sim.lifecycle.deaths == 1
    assert sim.agents.next_id > sim.config.agents.initial_population + 1
    path = tmp_path / "reused.alife"
    save_checkpoint(sim, path)
    restored = load_checkpoint(path)
    assert restored.agents.living() == sim.agents.living()
    assert restored.agents.state_hash() == sim.agents.state_hash()
    assert restored.lifecycle.births == sim.lifecycle.births
    assert restored.lifecycle.deaths == sim.lifecycle.deaths
    for _ in range(20):
        assert restored.advance() == sim.advance()
        assert restored.state_hash() == sim.state_hash()


def test_extinct_store_preserves_allocator_and_dead_slot_hash() -> None:
    sim = SimulationBuilder.build(settings("random", max_age=3))
    for _ in range(5):
        sim.advance()
    assert len(sim.agents) == 0
    restored = restore_checkpoint(checkpoint_bytes(sim))
    assert restored.agents.next_id == 9
    assert restored.lifecycle.deaths == 8
    assert restored.state_hash() == sim.state_hash()
    assert restored.advance() == sim.advance()
    assert restored.state_hash() == sim.state_hash()


def rewrite_manifest(data: bytes, change: Callable[[dict[str, Any]], None]) -> bytes:
    with ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    change(manifest)
    files["manifest.json"] = json.dumps(manifest).encode()
    result = io.BytesIO()
    with ZipFile(result, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return result.getvalue()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: m.update(schema_version=99), "schema version"),
        (lambda m: m.update(model_version=99), "model version"),
        (lambda m: m.update(kind="world"), "not a full"),
        (lambda m: m["brains"][0].update(state_version=99), "brain-state version"),
        (lambda m: m["brains"][0].update(core="memory"), "selected population core"),
        (lambda m: m["agents"].update(next_id=1), "allocator"),
        (lambda m: m["brains"].append(m["brains"][0]), "brain identities"),
        (lambda m: m.update(births=300), "do not reconcile"),
        (lambda m: m.update(continuation_hash="0" * 64), "integrity"),
        (lambda m: m["files"].update({"world.npz": "0" * 64}), "checksum mismatch"),
    ],
)
def test_invalid_checkpoint_metadata_is_rejected(
    change: Callable[[dict[str, Any]], None],
    message: str,
) -> None:
    data = checkpoint_bytes(SimulationBuilder.build(settings()))
    with pytest.raises(ValueError, match=message):
        restore_checkpoint(rewrite_manifest(data, change))


def test_file_validation_and_no_overwrite(tmp_path: Path) -> None:
    sim = SimulationBuilder.build(settings())
    path = tmp_path / "saved.alife"
    save_checkpoint(sim, path)
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        save_checkpoint(sim, path)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    with pytest.raises(ValueError, match=r"\.alife"):
        save_checkpoint(sim, tmp_path / "wrong.npz")
    with pytest.raises(ValueError, match="invalid simulation checkpoint"):
        restore_checkpoint(b"not a zip")
    world_path = tmp_path / "environment.npz"
    sim.world.snapshot().save(world_path)
    with pytest.raises(ValueError, match="environment-only"):
        load_checkpoint(world_path)
    with pytest.raises(ValueError, match="environment-only"):
        WorldSnapshot.load(path)


def test_unregistered_brain_is_not_silently_discarded() -> None:
    class UnsupportedBrain:
        name = "random"

        def decide(self, observation: Observation, context: DecisionContext) -> Any:
            return RandomBrain(0.3).decide(observation, context)

    sim = SimulationBuilder.build(settings("random"))
    sim.brains.remove(1)
    sim.brains.register(1, UnsupportedBrain())
    with pytest.raises(ValueError, match="unsupported checkpoint core"):
        checkpoint_bytes(sim)


@pytest.mark.parametrize("change", ["dtype", "shape", "pickle", "missing"])
def test_invalid_agent_arrays_rejected_before_restore(change: str) -> None:
    data = checkpoint_bytes(SimulationBuilder.build(settings()))
    with ZipFile(io.BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    with np.load(io.BytesIO(files["agents.npz"]), allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    if change == "dtype":
        arrays["ids"] = arrays["ids"].astype(np.float64)
    elif change == "shape":
        arrays["age"] = arrays["age"][:-1]
    elif change == "pickle":
        arrays["ids"] = np.array([{"not": "a safe agent ID"}], dtype=object)
    else:
        del arrays["age"]
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    files["agents.npz"] = buffer.getvalue()
    manifest = json.loads(files["manifest.json"])
    manifest["files"]["agents.npz"] = hashlib.sha256(files["agents.npz"]).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode()
    rewritten = io.BytesIO()
    with ZipFile(rewritten, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    with pytest.raises(ValueError):
        restore_checkpoint(rewritten.getvalue())


def test_checkpoint_output_from_new_cli_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    checkpoint = tmp_path / "new.alife"
    assert (
        main(
            [
                "--config",
                "experiments/starvation.yaml",
                "run",
                "--ticks",
                "3",
                "--checkpoint-out",
                str(checkpoint),
                "--progress-every",
                "0",
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    assert load_checkpoint(checkpoint).state_hash() == summary["state_hash"]
    assert summary["tick"] == 3


def test_cli_resume_uses_embedded_config_and_additional_ticks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = SimulationBuilder.build(settings())
    for _ in range(7):
        sim.advance()
    source = tmp_path / "source.alife"
    save_checkpoint(sim, source)
    target = tmp_path / "next.alife"
    # Continuation must not require experiments/world.yaml in the working directory.
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "run",
                "--from-checkpoint",
                str(source),
                "--ticks",
                "4",
                "--checkpoint-out",
                str(target),
                "--progress-every",
                "2",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    for _ in range(4):
        sim.advance()
    assert json.loads(captured.out)["tick"] == 11
    assert json.loads(captured.out)["state_hash"] == sim.state_hash()
    assert "tick 9/11" in captured.err and "tick 11/11" in captured.err
    assert load_checkpoint(target).state_hash() == sim.state_hash()
    with pytest.raises(SystemExit):
        main(
            [
                "--config",
                "other.yaml",
                "run",
                "--from-checkpoint",
                str(source),
                "--ticks",
                "0",
            ]
        )


def test_browser_save_restore_is_atomic_and_keeps_results(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = create_app(settings(), results_directory=tmp_path)
        controller = app.state.simulation_controller
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post("/api/control/run", json={"ticks": 6})
                async with asyncio.timeout(5):
                    while controller.running:
                        await asyncio.sleep(0.001)
                before = (await client.get("/api/status")).json()
                response = await client.get("/api/checkpoint")
                assert response.status_code == 200
                data = response.content
                expected = restore_checkpoint(data)
                await client.post("/api/control/step")
                state = (await client.get("/api/world")).json()
                endpoint = (
                    f"/api/control/restore-checkpoint?expected_run_id={state['run_id']}"
                )
                assert (
                    await client.post(endpoint, content=b"broken")
                ).status_code == 422
                assert (await client.get("/api/world")).json() == state
                restored = (await client.post(endpoint, content=data)).json()
                assert restored["tick"] == 6
                assert restored["run_id"] != state["run_id"]
                assert not restored["control"]["running"]
                assert restored["control"]["target_tick"] is None
                assert restored["control"]["checkpoint_origin"]["tick"] == 6
                assert (await client.get("/api/status")).json()["state_hash"] == before[
                    "state_hash"
                ]
                assert (await client.post(endpoint, content=data)).status_code == 409
                await client.post("/api/control/run", json={"ticks": 3})
                assert (await client.get("/api/checkpoint")).status_code == 409
                endpoint = (
                    "/api/control/restore-checkpoint?expected_run_id="
                    f"{restored['run_id']}"
                )
                assert (await client.post(endpoint, content=data)).status_code == 409
                async with asyncio.timeout(5):
                    while controller.running:
                        await asyncio.sleep(0.001)
                for _ in range(3):
                    expected.advance()
                status = (await client.get("/api/status")).json()
                assert status["state_hash"] == expected.state_hash()
                result_id = status["completed_result_id"]
                result = (await client.get(f"/api/results/{result_id}")).json()
                assert result["manifest"]["checkpoint_origin"]["tick"] == 6
                assert result["summary"]["start_tick"] == 6
                assert len((await client.get("/api/results")).json()["results"]) == 2
        finally:
            await controller.close()

    asyncio.run(exercise())


def test_serve_from_checkpoint_starts_at_saved_tick_paused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sim = SimulationBuilder.build(settings("random"))
    for _ in range(5):
        sim.advance()
    path = tmp_path / "serve.alife"
    save_checkpoint(sim, path)
    observed = []

    def serve(app: Any, *, host: str, port: int) -> None:
        async def check() -> None:
            controller = app.state.simulation_controller
            try:
                state = await controller.status()
                assert state["tick"] == 5
                assert state["state_hash"] == sim.state_hash()
                assert not state["running"]
                assert state["checkpoint_origin"]["tick"] == 5
                observed.append((host, port))
            finally:
                await controller.close()

        asyncio.run(check())

    monkeypatch.setattr("agentic_simulation.cli.uvicorn.run", serve)
    assert (
        main(
            [
                "serve",
                "--from-checkpoint",
                str(path),
                "--port",
                "8081",
                "--results-dir",
                str(tmp_path / "results"),
            ]
        )
        == 0
    )
    assert observed == [("127.0.0.1", 8081)]


def test_restore_recovers_failed_run_and_rejects_oversized_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SimulationEngine.advance

    def fail(sim: SimulationEngine) -> None:
        sim.world.advance()
        raise ValueError("partial tick failed")

    async def exercise() -> None:
        app = create_app(settings(), results_directory=tmp_path)
        controller = app.state.simulation_controller
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                initial = (await client.get("/api/world")).json()
                data = (await client.get("/api/checkpoint")).content
                monkeypatch.setattr(SimulationEngine, "advance", fail)
                await client.post("/api/control/step")
                assert (await client.get("/api/checkpoint")).status_code == 409
                monkeypatch.setattr(SimulationEngine, "advance", original)
                endpoint = (
                    "/api/control/restore-checkpoint?expected_run_id="
                    f"{initial['run_id']}"
                )
                response = await client.post(endpoint, content=data)
                assert response.status_code == 200
                assert response.json()["tick"] == 0
                assert response.json()["control"]["error"] is None
                frame = (await client.get("/api/world")).json()
                monkeypatch.setattr(
                    "agentic_simulation.visualization.server.MAX_CHECKPOINT_BYTES", 8
                )
                endpoint = (
                    f"/api/control/restore-checkpoint?expected_run_id={frame['run_id']}"
                )
                assert (
                    await client.post(endpoint, content=b"123456789")
                ).status_code == 413
                assert (await client.get("/api/world")).json() == frame
        finally:
            await controller.close()

    asyncio.run(exercise())
