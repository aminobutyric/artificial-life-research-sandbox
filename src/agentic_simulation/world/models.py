"""Domain types shared by world systems."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray


class TerrainType(IntEnum):
    PLAIN = 0
    FOREST = 1
    WATER = 2
    MOUNTAIN = 3


class ResourceType(StrEnum):
    FOOD = "food"
    WATER = "water"


@dataclass(frozen=True, slots=True, order=True)
class Position:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class TerrainProperties:
    traversable: bool
    movement_cost: float
    fertility_multiplier: float


TERRAIN_PROPERTIES: dict[TerrainType, TerrainProperties] = {
    TerrainType.PLAIN: TerrainProperties(True, 1.0, 1.0),
    TerrainType.FOREST: TerrainProperties(True, 1.4, 0.90),
    TerrainType.WATER: TerrainProperties(False, 2.5, 0.0),
    TerrainType.MOUNTAIN: TerrainProperties(False, 3.0, 0.05),
}


@dataclass(slots=True)
class EnvironmentLayers:
    """Dense world data. Arrays are always indexed as ``[y, x]``."""

    terrain: NDArray[np.uint8]
    elevation: NDArray[np.float32]
    base_temperature: NDArray[np.float32]
    temperature: NDArray[np.float32]
    moisture: NDArray[np.float32]
    fertility: NDArray[np.float32]

    def copy(self) -> EnvironmentLayers:
        return EnvironmentLayers(
            terrain=self.terrain.copy(),
            elevation=self.elevation.copy(),
            base_temperature=self.base_temperature.copy(),
            temperature=self.temperature.copy(),
            moisture=self.moisture.copy(),
            fertility=self.fertility.copy(),
        )


@dataclass(frozen=True, slots=True)
class ResourceView:
    id: int
    resource_type: ResourceType
    position: Position
    amount: float
    capacity: float
    regeneration_rate: float


@dataclass(frozen=True, slots=True)
class CellView:
    position: Position
    terrain: TerrainType
    traversable: bool
    movement_cost: float
    elevation: float
    temperature: float
    moisture: float
    fertility: float
    resources: tuple[ResourceView, ...]


@dataclass(frozen=True, slots=True)
class RegionView:
    center: Position
    radius: float
    cells: tuple[CellView, ...]
    resources: tuple[ResourceView, ...]


@dataclass(frozen=True, slots=True)
class ResourceExtraction:
    resource_id: int
    requested: float
    extracted: float
    remaining: float


@dataclass(frozen=True, slots=True)
class WorldEvent:
    schema_version: int
    tick: int
    sequence: int
    event_type: str
    position: Position | None
    metadata: dict[str, Any]
