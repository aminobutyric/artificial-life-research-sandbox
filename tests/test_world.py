from pathlib import Path

import numpy as np
import pytest

from agentic_simulation.config import (
    ResourceConfig,
    ResourcesConfig,
    WorldConfig,
)
from agentic_simulation.world import Position, ResourceType, WorldBuilder, WorldSnapshot


def small_config(seed: int = 7) -> WorldConfig:
    return WorldConfig(
        seed=seed,
        width=24,
        height=16,
        resources=ResourcesConfig(
            food=ResourceConfig(
                initial_nodes=18,
                capacity=20.0,
                regeneration_rate=0.5,
            ),
            water=ResourceConfig(
                initial_nodes=8,
                capacity=30.0,
                regeneration_rate=0.75,
            ),
        ),
    )


def test_generation_is_deterministic() -> None:
    first = WorldBuilder.build(small_config())
    second = WorldBuilder.build(small_config())

    assert first.state_hash() == second.state_hash()
    assert first.visualization_frame() == second.visualization_frame()
    assert WorldBuilder.build(small_config(8)).state_hash() != first.state_hash()
    assert (
        sum(item.resource_type is ResourceType.FOOD for item in first.resources()) == 18
    )
    assert (
        sum(item.resource_type is ResourceType.WATER for item in first.resources()) == 8
    )


def test_bounds_and_euclidean_region() -> None:
    world = WorldBuilder.build(small_config())

    assert world.contains(Position(0, 0))
    assert not world.contains(Position(-1, 0))
    assert len(world.observe_region(Position(4, 4), 1.0).cells) == 5
    assert len(world.observe_region(Position(0, 0), 1.0).cells) == 3
    with pytest.raises(IndexError, match="outside world bounds"):
        world.cell_at(Position(world.width, 0))


def test_extraction_is_atomic_and_regeneration_is_capped() -> None:
    world = WorldBuilder.build(small_config())
    resource = world.resources_near(Position(12, 8), 100, ResourceType.FOOD)[0]

    extraction = world.extract_resource(resource.id, resource.capacity * 2)
    assert extraction.extracted == pytest.approx(resource.amount)
    assert extraction.remaining == 0.0

    world.advance()
    regenerated = next(item for item in world.resources() if item.id == resource.id)
    assert regenerated.amount == pytest.approx(resource.regeneration_rate)
    assert regenerated.amount <= regenerated.capacity


def test_temperature_cycle_and_frames() -> None:
    config = small_config()
    world = WorldBuilder.build(config)
    initial = np.array(world.visualization_frame()["layers"]["temperature"])

    for _ in range(config.day_length_ticks):
        world.advance()

    current = np.array(world.visualization_frame()["layers"]["temperature"])
    assert np.allclose(initial, current, atol=0.001)
    assert len(world.visualization_frame()["layers"]["terrain"]) == (
        config.width * config.height
    )

    world.advance()
    update = world.visualization_update()
    assert update["kind"] == "update"
    assert "layers" not in update
    assert len(update["resource_amounts"]) == 26


def test_snapshot_round_trip_continues_exactly(tmp_path: Path) -> None:
    uninterrupted = WorldBuilder.build(small_config())
    resumed_source = WorldBuilder.build(small_config())
    for _ in range(19):
        uninterrupted.advance()
        resumed_source.advance()

    snapshot_path = tmp_path / "world.npz"
    snapshot = resumed_source.snapshot()
    snapshot.save(snapshot_path)
    with pytest.raises(FileExistsError, match="already exists"):
        snapshot.save(snapshot_path)
    resumed = WorldBuilder.from_snapshot(WorldSnapshot.load(snapshot_path))
    assert resumed.state_hash() == uninterrupted.state_hash()

    for _ in range(20):
        uninterrupted.advance()
        resumed.advance()
    assert resumed.state_hash() == uninterrupted.state_hash()
