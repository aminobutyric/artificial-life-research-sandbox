"""Seeded procedural generation for world layers and resources."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from agentic_simulation.config import ResourceConfig, WorldConfig
from agentic_simulation.world.models import (
    TERRAIN_PROPERTIES,
    EnvironmentLayers,
    Position,
    ResourceType,
    TerrainType,
)
from agentic_simulation.world.resources import ResourceStore


def _smooth(field: NDArray[np.float32], passes: int) -> NDArray[np.float32]:
    result = field
    for _ in range(passes):
        padded = np.pad(result, 1, mode="edge")
        averaged = sum(
            (
                padded[y : y + result.shape[0], x : x + result.shape[1]]
                for y in range(3)
                for x in range(3)
            ),
            start=np.zeros_like(result),
        ) / np.float32(9.0)
        result = np.asarray(averaged, dtype=np.float32)
    minimum = float(result.min())
    spread = float(result.max()) - minimum
    if spread == 0.0:
        return np.zeros_like(result, dtype=np.float32)
    return np.asarray((result - minimum) / spread, dtype=np.float32)


def generate_layers(
    config: WorldConfig,
    rng: np.random.Generator,
) -> EnvironmentLayers:
    shape = (config.height, config.width)
    elevation = _smooth(
        rng.random(shape, dtype=np.float32), config.generation.smoothing_passes
    )
    moisture_noise = _smooth(
        rng.random(shape, dtype=np.float32), config.generation.smoothing_passes
    )
    moisture = np.clip(
        np.float32(0.70) * moisture_noise
        + np.float32(0.30) * (np.float32(1.0) - elevation),
        0.0,
        1.0,
    ).astype(np.float32)

    terrain = np.full(shape, TerrainType.PLAIN, dtype=np.uint8)
    terrain[elevation < config.generation.water_level] = TerrainType.WATER
    terrain[elevation > config.generation.mountain_level] = TerrainType.MOUNTAIN
    forest = (terrain == TerrainType.PLAIN) & (
        moisture >= config.generation.forest_moisture
    )
    terrain[forest] = TerrainType.FOREST

    latitude = np.abs(np.linspace(-1.0, 1.0, config.height, dtype=np.float32))
    latitude = np.broadcast_to(latitude[:, None], shape)
    base_temperature = (
        np.float32(27.0) - np.float32(18.0) * latitude - np.float32(12.0) * elevation
    ).astype(np.float32)

    comfort = np.clip(
        1.0 - np.abs(base_temperature - np.float32(18.0)) / np.float32(30.0),
        0.0,
        1.0,
    )
    fertility = (np.float32(0.65) * moisture + np.float32(0.35) * comfort).astype(
        np.float32
    )
    multipliers = np.array(
        [
            TERRAIN_PROPERTIES[TerrainType(index)].fertility_multiplier
            for index in range(len(TerrainType))
        ],
        dtype=np.float32,
    )
    fertility *= multipliers[terrain]
    temperature = base_temperature.copy()
    return EnvironmentLayers(
        terrain=terrain,
        elevation=elevation,
        base_temperature=base_temperature,
        temperature=temperature,
        moisture=moisture,
        fertility=fertility,
    )


def _positions_from_mask(mask: NDArray[np.bool_]) -> list[Position]:
    ys, xs = np.nonzero(mask)
    return [Position(int(x), int(y)) for y, x in zip(ys, xs, strict=True)]


def _sample_positions(
    candidates: list[Position],
    count: int,
    weights: NDArray[np.float64],
    rng: np.random.Generator,
) -> list[Position]:
    sample_size = min(count, len(candidates))
    if sample_size == 0:
        return []
    normalized = weights / weights.sum() if weights.sum() > 0.0 else None
    indexes = rng.choice(len(candidates), size=sample_size, replace=False, p=normalized)
    return [candidates[int(index)] for index in np.sort(indexes)]


def _add_nodes(
    store: ResourceStore,
    resource_type: ResourceType,
    positions: list[Position],
    settings: ResourceConfig,
    rng: np.random.Generator,
) -> None:
    for position in positions:
        fill = rng.uniform(settings.initial_fill_min, settings.initial_fill_max)
        store.add(
            resource_type,
            position,
            float(settings.capacity * fill),
            settings.capacity,
            settings.regeneration_rate,
        )


def generate_resources(
    config: WorldConfig,
    layers: EnvironmentLayers,
    rng: np.random.Generator,
) -> ResourceStore:
    store = ResourceStore()
    traversable = (layers.terrain == TerrainType.PLAIN) | (
        layers.terrain == TerrainType.FOREST
    )

    food_candidates = _positions_from_mask(traversable)
    food_weights = np.array(
        [
            layers.fertility[position.y, position.x] + 0.01
            for position in food_candidates
        ],
        dtype=np.float64,
    )
    food_positions = _sample_positions(
        food_candidates,
        config.resources.food.initial_nodes,
        food_weights,
        rng,
    )
    _add_nodes(store, ResourceType.FOOD, food_positions, config.resources.food, rng)

    water_cells = layers.terrain == TerrainType.WATER
    padded = np.pad(water_cells, 1, mode="constant", constant_values=False)
    near_water = np.zeros_like(water_cells)
    for y_offset, x_offset in ((0, 1), (1, 0), (1, 2), (2, 1)):
        near_water |= padded[
            y_offset : y_offset + config.height,
            x_offset : x_offset + config.width,
        ]
    water_candidates = _positions_from_mask(traversable)
    water_weights = np.array(
        [
            (layers.moisture[position.y, position.x] + 0.01)
            * (10.0 if near_water[position.y, position.x] else 1.0)
            for position in water_candidates
        ],
        dtype=np.float64,
    )
    water_positions = _sample_positions(
        water_candidates,
        config.resources.water.initial_nodes,
        water_weights,
        rng,
    )
    _add_nodes(store, ResourceType.WATER, water_positions, config.resources.water, rng)
    return store
