"""Portable, versioned snapshots for exact world continuation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from agentic_simulation.config import WorldConfig
from agentic_simulation.world.models import EnvironmentLayers, ResourceView

SNAPSHOT_SCHEMA_VERSION = 1


def _json_default(value: object) -> object:
    if isinstance(value, np.integer | np.floating):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"cannot serialize {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    config: WorldConfig
    tick: int
    layers: EnvironmentLayers
    resources: tuple[ResourceView, ...]
    next_resource_id: int
    rng_state: dict[str, Any]

    def save(self, path: str | Path) -> None:
        output = Path(path)
        if output.suffix != ".npz":
            raise ValueError("snapshot path must end with .npz")
        if output.exists():
            raise FileExistsError(f"snapshot already exists: {output}")
        metadata = {
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "config": self.config.model_dump(mode="json"),
            "tick": self.tick,
            "next_resource_id": self.next_resource_id,
            "rng_state": self.rng_state,
            "resources": [
                {
                    "id": resource.id,
                    "resource_type": resource.resource_type.value,
                    "x": resource.position.x,
                    "y": resource.position.y,
                    "amount": resource.amount,
                    "capacity": resource.capacity,
                    "regeneration_rate": resource.regeneration_rate,
                }
                for resource in self.resources
            ],
        }
        np.savez_compressed(
            output,
            metadata=np.array(json.dumps(metadata, default=_json_default)),
            terrain=self.layers.terrain,
            elevation=self.layers.elevation,
            base_temperature=self.layers.base_temperature,
            temperature=self.layers.temperature,
            moisture=self.layers.moisture,
            fertility=self.layers.fertility,
        )

    @classmethod
    def load(cls, path: str | Path) -> WorldSnapshot:
        with np.load(Path(path), allow_pickle=False) as archive:
            metadata = json.loads(str(archive["metadata"].item()))
            if metadata.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
                raise ValueError("unsupported world snapshot schema")
            config = WorldConfig.model_validate(metadata["config"])
            layers = EnvironmentLayers(
                terrain=_copy_array(archive["terrain"], np.uint8),
                elevation=_copy_array(archive["elevation"], np.float32),
                base_temperature=_copy_array(archive["base_temperature"], np.float32),
                temperature=_copy_array(archive["temperature"], np.float32),
                moisture=_copy_array(archive["moisture"], np.float32),
                fertility=_copy_array(archive["fertility"], np.float32),
            )
        resources = tuple(_resource_from_dict(item) for item in metadata["resources"])
        return cls(
            config=config,
            tick=int(metadata["tick"]),
            layers=layers,
            resources=resources,
            next_resource_id=int(metadata["next_resource_id"]),
            rng_state=metadata["rng_state"],
        )


def _copy_array(array: NDArray[Any], dtype: type[np.generic]) -> NDArray[Any]:
    return np.asarray(array, dtype=dtype).copy()


def _resource_from_dict(data: dict[str, Any]) -> ResourceView:
    from agentic_simulation.world.models import Position, ResourceType

    return ResourceView(
        id=int(data["id"]),
        resource_type=ResourceType(data["resource_type"]),
        position=Position(int(data["x"]), int(data["y"])),
        amount=float(data["amount"]),
        capacity=float(data["capacity"]),
        regeneration_rate=float(data["regeneration_rate"]),
    )
