"""Versioned full-state archives. Only JSON and non-pickled NumPy arrays are read."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from math import prod
from pathlib import Path
from typing import Any, Literal
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile
from zlib import error as DecompressionError

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from agentic_simulation.agents import AgentSpatialIndex, AgentStore
from agentic_simulation.agents.store import SavedAgentState
from agentic_simulation.brains import BrainRegistry, BrainSystem
from agentic_simulation.brains.checkpoint import dump_brain, restore_brain
from agentic_simulation.config import AppConfig, StrictModel
from agentic_simulation.world import WorldBuilder, WorldSnapshot

from .engine import SimulationEngine

CHECKPOINT_VERSION = 1
MODEL_VERSION = 1
MAX_CHECKPOINT_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
ARCHIVE_FILES = {"manifest.json", "world.npz", "agents.npz"}


class BrainEntry(StrictModel):
    agent_id: int = Field(ge=1, strict=True)
    core: str
    state_version: int = Field(ge=1, strict=True)
    state: dict[str, Any]


class Manifest(StrictModel):
    kind: Literal["alife-simulation-checkpoint"]
    schema_version: Literal[1]
    model_version: Literal[1]
    numpy_version: str
    config: AppConfig
    births: int = Field(ge=0, strict=True)
    deaths: int = Field(ge=0, strict=True)
    brain_seed: int = Field(ge=0, strict=True)
    brains: tuple[BrainEntry, ...]
    agents: SavedAgentState
    files: dict[str, str]
    state_hash: str
    continuation_hash: str


def _brain_entries(simulation: SimulationEngine) -> list[dict[str, Any]]:
    return [
        {"agent_id": agent.id, **dump_brain(simulation.brains.get(agent.id))}
        for agent in simulation.agents.living()
    ]


def continuation_hash(simulation: SimulationEngine) -> str:
    """Include decision state without changing the existing experiment hash format."""
    payload = {
        "state_hash": simulation.state_hash(),
        "brain_seed": simulation.decision_seed,
        "brains": _brain_entries(simulation),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def checkpoint_bytes(simulation: SimulationEngine) -> bytes:
    agent_metadata, agent_arrays = simulation.agents.checkpoint_state()
    agents = io.BytesIO()
    np.savez_compressed(
        agents,
        ids=agent_arrays["ids"],
        x=agent_arrays["x"],
        y=agent_arrays["y"],
        health=agent_arrays["health"],
        energy=agent_arrays["energy"],
        age=agent_arrays["age"],
        vision=agent_arrays["vision"],
        alive=agent_arrays["alive"],
    )
    files = {
        "agents.npz": agents.getvalue(),
        "world.npz": simulation.world.snapshot().to_bytes(),
    }
    manifest = Manifest.model_validate(
        {
            "kind": "alife-simulation-checkpoint",
            "schema_version": CHECKPOINT_VERSION,
            "model_version": MODEL_VERSION,
            "numpy_version": np.__version__,
            "config": simulation.config,
            "births": simulation.lifecycle.births,
            "deaths": simulation.lifecycle.deaths,
            "brain_seed": simulation.decision_seed,
            "brains": _brain_entries(simulation),
            "agents": agent_metadata,
            "files": {
                name: hashlib.sha256(data).hexdigest() for name, data in files.items()
            },
            "state_hash": simulation.state_hash(),
            "continuation_hash": continuation_hash(simulation),
        }
    )
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest.model_dump_json())
        for name, data in files.items():
            archive.writestr(name, data)
    data = output.getvalue()
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise ValueError("checkpoint exceeds the 64 MiB archive limit")
    return data


def save_checkpoint(simulation: SimulationEngine, path: str | Path) -> None:
    output = Path(path)
    if output.suffix != ".alife":
        raise ValueError("full simulation checkpoint path must end with .alife")
    if output.exists():
        raise FileExistsError(f"checkpoint already exists: {output}")
    data = checkpoint_bytes(simulation)
    # Publish a fully written file atomically, without replacing an existing target.
    with tempfile.NamedTemporaryFile(
        dir=output.parent, suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


def _checked_zip(data: bytes, expected: set[str] | None = None) -> ZipFile:
    archive = ZipFile(io.BytesIO(data))
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)) or (
        expected is not None and set(names) != expected
    ):
        archive.close()
        raise ValueError(
            "checkpoint archive has missing, duplicate, or unexpected files"
        )
    if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        archive.close()
        raise ValueError("checkpoint exceeds the 512 MiB expanded-data limit")
    return archive


def _arrays(data: bytes) -> dict[str, NDArray[Any]]:
    with _checked_zip(data) as container:
        declared_bytes = 0
        for entry in container.infolist():
            with container.open(entry) as stream:
                version = np.lib.format.read_magic(stream)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                else:
                    raise ValueError("unsupported checkpoint NumPy array format")
                if dtype.hasobject:
                    raise ValueError(
                        "object/pickle arrays are forbidden in checkpoints"
                    )
                size = prod(shape) * dtype.itemsize
                declared_bytes += size
                if declared_bytes > MAX_EXPANDED_BYTES:
                    raise ValueError(
                        "checkpoint array header exceeds expanded-data limit"
                    )
                if stream.tell() + size != entry.file_size:
                    raise ValueError(
                        "checkpoint array header does not match its payload"
                    )
        with np.load(io.BytesIO(data), allow_pickle=False) as archive:
            return {name: archive[name].copy() for name in archive.files}


def restore_checkpoint(data: bytes) -> SimulationEngine:
    try:
        return _restore_checkpoint(data)
    except (
        BadZipFile,
        DecompressionError,
        KeyError,
        IndexError,
        TypeError,
        OverflowError,
        EOFError,
        RuntimeError,
        NotImplementedError,
    ) as error:
        raise ValueError(f"invalid simulation checkpoint: {error}") from error


def _restore_checkpoint(data: bytes) -> SimulationEngine:
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise ValueError("checkpoint exceeds the 64 MiB archive limit")
    with _checked_zip(data) as archive:
        if "manifest.json" not in archive.namelist():
            raise ValueError(
                "not a full simulation checkpoint; "
                "environment-only .npz snapshots cannot restore agents"
            )
        if set(archive.namelist()) != ARCHIVE_FILES:
            raise ValueError("checkpoint archive has missing or unexpected files")
        if archive.getinfo("manifest.json").file_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("checkpoint manifest exceeds the 64 MiB limit")
        raw = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(raw, dict)
            or raw.get("kind") != "alife-simulation-checkpoint"
        ):
            raise ValueError("not a full simulation checkpoint")
        if (
            type(raw.get("schema_version")) is not int
            or raw["schema_version"] != CHECKPOINT_VERSION
        ):
            raise ValueError("unsupported simulation checkpoint schema version")
        if (
            type(raw.get("model_version")) is not int
            or raw["model_version"] != MODEL_VERSION
        ):
            raise ValueError("unsupported simulation model version")
        metadata = Manifest.model_validate(raw)
        if set(metadata.files) != {"agents.npz", "world.npz"}:
            raise ValueError("checkpoint payload checksums are missing")
        payloads = {name: archive.read(name) for name in metadata.files}
    for name, payload in payloads.items():
        if hashlib.sha256(payload).hexdigest() != metadata.files[name]:
            raise ValueError(f"checkpoint checksum mismatch: {name}")
    expanded_bytes = 0
    for payload in payloads.values():
        with _checked_zip(payload) as nested:
            expanded_bytes += sum(entry.file_size for entry in nested.infolist())
    if expanded_bytes > MAX_EXPANDED_BYTES:
        raise ValueError("checkpoint exceeds the 512 MiB expanded-data limit")
    world_arrays = _arrays(payloads["world.npz"])
    expected_layers = {
        "terrain",
        "elevation",
        "base_temperature",
        "temperature",
        "moisture",
        "fertility",
    }
    if set(world_arrays) != expected_layers | {"metadata"}:
        raise ValueError("checkpoint world arrays are missing or unexpected")
    for name in expected_layers:
        layer = world_arrays[name]
        dtype = np.dtype("uint8" if name == "terrain" else "float32")
        if layer.dtype != dtype or layer.shape != (
            metadata.config.world.height,
            metadata.config.world.width,
        ):
            raise ValueError(f"invalid checkpoint world array: {name}")
        if not np.isfinite(layer).all():
            raise ValueError(f"checkpoint world array must be finite: {name}")
    if np.any(world_arrays["terrain"] > 3):
        raise ValueError("invalid checkpoint terrain code")
    snapshot = WorldSnapshot.from_bytes(payloads["world.npz"])
    if snapshot.config != metadata.config.world:
        raise ValueError("checkpoint world configuration does not match manifest")
    if snapshot.next_resource_id <= max((r.id for r in snapshot.resources), default=0):
        raise ValueError("checkpoint resource ID allocator would reuse an ID")
    for resource in snapshot.resources:
        if not all(
            np.isfinite(value)
            for value in (
                resource.amount,
                resource.capacity,
                resource.regeneration_rate,
            )
        ):
            raise ValueError("checkpoint resources must be finite")
    world = WorldBuilder.from_snapshot(snapshot)
    agents = AgentStore.from_checkpoint(
        metadata.agents.model_dump(), _arrays(payloads["agents.npz"])
    )
    if (
        len(agents)
        != metadata.config.agents.initial_population + metadata.births - metadata.deaths
    ):
        raise ValueError(
            "checkpoint population and lifecycle counters do not reconcile"
        )
    spatial = AgentSpatialIndex()
    registry = BrainRegistry()
    entries = {entry.agent_id: entry for entry in metadata.brains}
    if len(entries) != len(metadata.brains) or set(entries) != {
        a.id for a in agents.living()
    }:
        raise ValueError("checkpoint brain identities do not match living agents")
    for agent in agents.living():
        if not world.cell_at(agent.position).traversable:
            raise ValueError("checkpoint agent occupies an impassable cell")
        if agent.age > world.tick or agent.last_reproduction > world.tick:
            raise ValueError("checkpoint agent age/reproduction exceeds the world tick")
        entry = entries[agent.id]
        if entry.core != metadata.config.agents.brain:
            raise ValueError(
                "checkpoint core does not match the selected population core"
            )
        brain = restore_brain(entry.core, entry.state_version, entry.state)
        spatial.add(agent.id, agent.position)
        registry.register(agent.id, brain)
    simulation = SimulationEngine(
        metadata.config,
        world,
        agents,
        spatial,
        registry,
        BrainSystem(registry, metadata.brain_seed),
    )
    simulation.lifecycle.births = metadata.births
    simulation.lifecycle.deaths = metadata.deaths
    if (
        simulation.state_hash() != metadata.state_hash
        or continuation_hash(simulation) != metadata.continuation_hash
    ):
        raise ValueError("checkpoint state integrity verification failed")
    return simulation


def load_checkpoint(path: str | Path) -> SimulationEngine:
    source = Path(path)
    if source.stat().st_size > MAX_CHECKPOINT_BYTES:
        raise ValueError("checkpoint exceeds the 64 MiB archive limit")
    return restore_checkpoint(source.read_bytes())
