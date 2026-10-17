"""A cheap baseline brain that selects among valid local movements."""

from __future__ import annotations

from typing import Any

from agentic_simulation.actions import ActionIntent, ActionType
from agentic_simulation.agents import Observation

from .base import DecisionContext


class RandomBrain:
    state_version = 1

    def __init__(self, idle_probability: float) -> None:
        if not 0.0 <= idle_probability <= 1.0:
            raise ValueError("idle probability must be between zero and one")
        self._idle_probability = idle_probability

    @property
    def name(self) -> str:
        return "random"

    def checkpoint_state(self) -> dict[str, Any]:
        return {"idle_probability": self._idle_probability}

    def decide(
        self, observation: Observation, context: DecisionContext
    ) -> ActionIntent:
        origin = observation.self_state.position
        candidates = tuple(
            cell.position
            for cell in observation.nearby_cells
            if cell.traversable
            and abs(cell.position.x - origin.x) + abs(cell.position.y - origin.y) == 1
        )
        if not candidates or context.rng.random() < self._idle_probability:
            return ActionIntent(observation.self_state.id, ActionType.IDLE)
        destination = candidates[int(context.rng.integers(0, len(candidates)))]
        return ActionIntent(
            observation.self_state.id,
            ActionType.MOVE,
            target_position=destination,
        )
