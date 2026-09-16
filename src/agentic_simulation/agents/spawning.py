"""Deterministic initial population placement."""

from __future__ import annotations

import numpy as np

from agentic_simulation.config import AgentsConfig
from agentic_simulation.world import World

from .genome import Genome
from .spatial import AgentSpatialIndex
from .store import AgentStore


def spawn_initial_population(
    world: World,
    config: AgentsConfig,
    rng: np.random.Generator,
) -> tuple[AgentStore, AgentSpatialIndex]:
    positions = world.traversable_positions()
    if config.initial_population and not positions:
        raise ValueError("cannot spawn agents: the world has no traversable cells")

    store = AgentStore(initial_capacity=config.initial_population)
    spatial_index = AgentSpatialIndex()
    for _ in range(config.initial_population):
        position = positions[int(rng.integers(0, len(positions)))]
        genome = Genome.random(
            np.random.default_rng(
                np.random.SeedSequence([world.config.seed, 0x6EAE, store.next_id])
            )
        )
        agent = store.create(
            position,
            health=config.initial_health,
            energy=config.initial_energy,
            vision_radius=genome.vision_radius(config.vision_radius),
            genome=genome,
        )
        spatial_index.add(agent.id, position)
    return store, spatial_index
