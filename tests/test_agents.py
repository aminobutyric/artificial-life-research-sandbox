from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from agentic_simulation.agents import (
    AgentSpatialIndex,
    AgentStore,
    spawn_initial_population,
)
from agentic_simulation.config import AgentsConfig, WorldConfig
from agentic_simulation.world import Position, WorldBuilder


def test_agent_store_has_stable_ids_and_immutable_views() -> None:
    store = AgentStore(initial_capacity=1)
    created = [
        store.create(Position(index, 0), health=100.0, energy=50.0, vision_radius=3.0)
        for index in range(20)
    ]

    assert [agent.id for agent in created] == list(range(1, 21))
    assert len(store) == 20
    assert store.next_id == 21
    with pytest.raises(FrozenInstanceError):
        created[0].age = 10  # type: ignore[misc]

    moved = store.move(1, Position(2, 3), 0.5)
    assert moved.position == Position(2, 3)
    assert moved.energy == pytest.approx(49.5)


def test_spatial_index_queries_are_sorted_and_exact() -> None:
    index = AgentSpatialIndex()
    index.add(3, Position(5, 5))
    index.add(1, Position(4, 5))
    index.add(2, Position(6, 6))

    assert index.near(Position(5, 5), 1.0) == (1, 3)
    assert index.near(Position(5, 5), 2.0, exclude_id=3) == (1, 2)

    index.move(1, Position(6, 5))
    assert index.at(Position(4, 5)) == ()
    assert index.at(Position(6, 5)) == (1,)


def test_initial_spawning_is_seeded_and_uses_traversable_cells() -> None:
    world = WorldBuilder.build(WorldConfig(seed=17, width=30, height=20))
    config = AgentsConfig(initial_population=80)
    first, _ = spawn_initial_population(world, config, np.random.default_rng(123))
    second, _ = spawn_initial_population(world, config, np.random.default_rng(123))

    assert first.state_hash() == second.state_hash()
    assert len(first) == 80
    assert all(world.cell_at(agent.position).traversable for agent in first.living())
