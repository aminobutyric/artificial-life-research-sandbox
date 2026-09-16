from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from agentic_simulation.actions import ActionType
from agentic_simulation.agents import Observation, SelfObservation
from agentic_simulation.agents.genome import Genome
from agentic_simulation.brains import DecisionContext
from agentic_simulation.brains.genetic import GeneticBrain
from agentic_simulation.config import LifeConfig
from agentic_simulation.world import (
    CellView,
    Position,
    ResourceType,
    ResourceView,
    TerrainType,
)


def observation(food_position: Position) -> Observation:
    positions = (
        Position(0, 0),
        Position(0, 1),
        Position(1, 1),
        Position(2, 1),
        Position(2, 0),
    )
    cells = tuple(
        CellView(p, TerrainType.PLAIN, True, 1, 0, 20, 0.5, 0.5, ()) for p in positions
    )
    food = ResourceView(1, ResourceType.FOOD, food_position, 10, 10, 0)
    return Observation(
        SelfObservation(1, Position(0, 0), 100, 50, 0, Genome(food_attraction=1)),
        (),
        (food,),
        cells,
        0,
    )


def test_genetic_brain_routes_around_unobserved_or_blocked_cells() -> None:
    brain = GeneticBrain(LifeConfig())
    seen = observation(Position(2, 0))
    decision = brain.decide(seen, DecisionContext(0, np.random.default_rng(1)))
    assert decision.action_type is ActionType.MOVE
    assert decision.target_position == Position(0, 1)
    with pytest.raises(FrozenInstanceError):
        seen.self_state.genome.vision = 1  # type: ignore[misc]


def test_genetic_brain_eats_local_food_before_moving() -> None:
    brain = GeneticBrain(LifeConfig())
    decision = brain.decide(
        observation(Position(0, 0)), DecisionContext(0, np.random.default_rng(1))
    )
    assert decision.action_type is ActionType.EAT
    assert decision.target_resource == 1
