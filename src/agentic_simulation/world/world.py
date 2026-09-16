"""Agent-agnostic world facade and construction."""

from __future__ import annotations

import hashlib
import json
from math import ceil, floor, hypot
from typing import Any

import numpy as np

from agentic_simulation.config import WorldConfig
from agentic_simulation.world.environment import EnvironmentSystem
from agentic_simulation.world.generation import generate_layers, generate_resources
from agentic_simulation.world.models import (
    TERRAIN_PROPERTIES,
    CellView,
    EnvironmentLayers,
    Position,
    RegionView,
    ResourceExtraction,
    ResourceType,
    ResourceView,
    TerrainType,
    WorldEvent,
)
from agentic_simulation.world.resources import ResourceStore
from agentic_simulation.world.snapshot import WorldSnapshot


class World:
    """Own world state and expose immutable observations and controlled updates."""

    def __init__(
        self,
        config: WorldConfig,
        layers: EnvironmentLayers,
        resources: ResourceStore,
        rng: np.random.Generator,
        *,
        tick: int = 0,
    ) -> None:
        self.config = config
        self._layers = layers
        self._resources = resources
        self._rng = rng
        self._environment = EnvironmentSystem(config)
        self.tick = tick
        self._validate_state()

    @property
    def width(self) -> int:
        return self.config.width

    @property
    def height(self) -> int:
        return self.config.height

    def contains(self, position: Position) -> bool:
        return 0 <= position.x < self.width and 0 <= position.y < self.height

    def cell_at(self, position: Position) -> CellView:
        self._require_position(position)
        terrain = TerrainType(int(self._layers.terrain[position.y, position.x]))
        properties = TERRAIN_PROPERTIES[terrain]
        return CellView(
            position=position,
            terrain=terrain,
            traversable=properties.traversable,
            movement_cost=properties.movement_cost,
            elevation=float(self._layers.elevation[position.y, position.x]),
            temperature=float(self._layers.temperature[position.y, position.x]),
            moisture=float(self._layers.moisture[position.y, position.x]),
            fertility=float(self._layers.fertility[position.y, position.x]),
            resources=self._resources.at(position),
        )

    def observe_region(self, center: Position, radius: float) -> RegionView:
        self._require_position(center)
        if radius < 0.0:
            raise ValueError("radius cannot be negative")
        cells: list[CellView] = []
        for y in range(
            max(0, floor(center.y - radius)),
            min(self.height, ceil(center.y + radius) + 1),
        ):
            for x in range(
                max(0, floor(center.x - radius)),
                min(self.width, ceil(center.x + radius) + 1),
            ):
                if hypot(x - center.x, y - center.y) <= radius:
                    cells.append(self.cell_at(Position(x, y)))
        return RegionView(
            center=center,
            radius=radius,
            cells=tuple(cells),
            resources=self._resources.near(center, radius),
        )

    def resources_near(
        self,
        center: Position,
        radius: float,
        resource_type: ResourceType | None = None,
    ) -> tuple[ResourceView, ...]:
        self._require_position(center)
        return self._resources.near(center, radius, resource_type)

    def resources(self) -> tuple[ResourceView, ...]:
        return self._resources.all()

    def traversable_positions(self) -> tuple[Position, ...]:
        """Return traversable cells in stable row-major order."""

        traversable = (self._layers.terrain == TerrainType.PLAIN) | (
            self._layers.terrain == TerrainType.FOREST
        )
        ys, xs = np.nonzero(traversable)
        return tuple(Position(int(x), int(y)) for y, x in zip(ys, xs, strict=True))

    def extract_resource(
        self, resource_id: int, requested_amount: float
    ) -> ResourceExtraction:
        return self._resources.extract(resource_id, requested_amount)

    def advance(self) -> tuple[WorldEvent, ...]:
        """Advance the environment exactly one tick."""

        next_tick = self.tick + 1
        self._environment.update(self._layers, next_tick)
        changed_nodes, regenerated = self._resources.regenerate()
        self.tick = next_tick
        return (
            WorldEvent(
                schema_version=1,
                tick=self.tick,
                sequence=0,
                event_type="environment_updated",
                position=None,
                metadata={
                    "resource_nodes_regenerated": changed_nodes,
                    "resource_amount_regenerated": regenerated,
                },
            ),
        )

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(
            config=self.config,
            tick=self.tick,
            layers=self._layers.copy(),
            resources=self._resources.all(),
            next_resource_id=self._resources.next_id,
            rng_state=json.loads(json.dumps(self._rng.bit_generator.state)),
        )

    def state_hash(self) -> str:
        """Return a stable hash of complete state, including future RNG state."""

        digest = hashlib.sha256()
        digest.update(self.config.model_dump_json().encode())
        digest.update(self.tick.to_bytes(8, byteorder="little", signed=False))
        digest.update(
            self._resources.next_id.to_bytes(8, byteorder="little", signed=False)
        )
        for layer in (
            self._layers.terrain,
            self._layers.elevation,
            self._layers.base_temperature,
            self._layers.temperature,
            self._layers.moisture,
            self._layers.fertility,
        ):
            digest.update(str(layer.dtype).encode())
            digest.update(str(layer.shape).encode())
            digest.update(layer.tobytes(order="C"))
        for resource in self._resources.all():
            digest.update(
                json.dumps(
                    {
                        "id": resource.id,
                        "type": resource.resource_type.value,
                        "x": resource.position.x,
                        "y": resource.position.y,
                        "amount": resource.amount,
                        "capacity": resource.capacity,
                        "regeneration_rate": resource.regeneration_rate,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
        digest.update(
            json.dumps(
                self._rng.bit_generator.state,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        return digest.hexdigest()

    def visualization_frame(self) -> dict[str, Any]:
        """Copy state into a JSON-compatible, read-only client representation."""

        terrain_counts = {
            terrain.name.lower(): int(np.count_nonzero(self._layers.terrain == terrain))
            for terrain in TerrainType
        }
        resources = self._resources.all()
        return {
            "kind": "world",
            "schema_version": 1,
            "tick": self.tick,
            "width": self.width,
            "height": self.height,
            "temperature_offset": self._temperature_offset(),
            "layers": {
                "terrain": self._layers.terrain.ravel().tolist(),
                "elevation": _rounded_flat(self._layers.elevation),
                "temperature": _rounded_flat(self._layers.temperature),
                "moisture": _rounded_flat(self._layers.moisture),
                "fertility": _rounded_flat(self._layers.fertility),
            },
            "resources": [
                {
                    "id": resource.id,
                    "type": resource.resource_type.value,
                    "x": resource.position.x,
                    "y": resource.position.y,
                    "amount": round(resource.amount, 3),
                    "capacity": resource.capacity,
                }
                for resource in resources
            ],
            "statistics": self._dynamic_statistics()
            | {
                "terrain_cells": terrain_counts,
            },
        }

    def visualization_update(self) -> dict[str, Any]:
        """Return a compact update for a client that already has static layers."""

        return {
            "kind": "update",
            "schema_version": 1,
            "tick": self.tick,
            "temperature_offset": self._temperature_offset(),
            "resource_amounts": [
                [resource.id, round(resource.amount, 3)]
                for resource in self._resources.all()
            ],
            "statistics": self._dynamic_statistics(),
        }

    def _temperature_offset(self) -> float:
        return round(
            float(self._layers.temperature[0, 0] - self._layers.base_temperature[0, 0]),
            4,
        )

    def _dynamic_statistics(self) -> dict[str, Any]:
        resources = self._resources.all()
        resource_totals = {
            resource_type.value: round(
                sum(
                    resource.amount
                    for resource in resources
                    if resource.resource_type is resource_type
                ),
                3,
            )
            for resource_type in ResourceType
        }
        return {
            "resource_nodes": len(resources),
            "resource_totals": resource_totals,
            "temperature_min": round(float(self._layers.temperature.min()), 2),
            "temperature_max": round(float(self._layers.temperature.max()), 2),
        }

    def _require_position(self, position: Position) -> None:
        if not self.contains(position):
            raise IndexError(f"position outside world bounds: {position}")

    def _validate_state(self) -> None:
        expected_shape = (self.height, self.width)
        layers = (
            ("terrain", self._layers.terrain),
            ("elevation", self._layers.elevation),
            ("base_temperature", self._layers.base_temperature),
            ("temperature", self._layers.temperature),
            ("moisture", self._layers.moisture),
            ("fertility", self._layers.fertility),
        )
        for name, layer in layers:
            if layer.shape != expected_shape:
                raise ValueError(
                    f"{name} has shape {layer.shape}; expected {expected_shape}"
                )
        if self.tick < 0:
            raise ValueError("world tick cannot be negative")
        for resource in self._resources.all():
            self._require_position(resource.position)


class WorldBuilder:
    """Construct worlds without exposing their mutable internals."""

    @staticmethod
    def build(config: WorldConfig) -> World:
        terrain_seed, resource_seed, runtime_seed = np.random.SeedSequence(
            config.seed
        ).spawn(3)
        layers = generate_layers(config, np.random.default_rng(terrain_seed))
        resources = generate_resources(
            config, layers, np.random.default_rng(resource_seed)
        )
        return World(
            config,
            layers,
            resources,
            np.random.default_rng(runtime_seed),
        )

    @staticmethod
    def from_snapshot(snapshot: WorldSnapshot) -> World:
        resources = ResourceStore(next_id=snapshot.next_resource_id)
        for resource in snapshot.resources:
            resources.add(
                resource.resource_type,
                resource.position,
                resource.amount,
                resource.capacity,
                resource.regeneration_rate,
                resource_id=resource.id,
            )
        rng = np.random.default_rng()
        rng.bit_generator.state = snapshot.rng_state
        return World(
            snapshot.config,
            snapshot.layers.copy(),
            resources,
            rng,
            tick=snapshot.tick,
        )


def _rounded_flat(layer: np.ndarray[Any, Any]) -> list[float]:
    return np.round(layer, 3).ravel().tolist()
