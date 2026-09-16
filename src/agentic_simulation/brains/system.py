"""Brain registration and deterministic batched decisions."""

from __future__ import annotations

import numpy as np

from agentic_simulation.actions import ActionIntent
from agentic_simulation.agents import Observation

from .base import Brain, DecisionContext


class BrainRegistry:
    def __init__(self) -> None:
        self._brains: dict[int, Brain] = {}

    def register(self, agent_id: int, brain: Brain) -> None:
        if agent_id in self._brains:
            raise ValueError(f"agent {agent_id} already has a brain")
        self._brains[agent_id] = brain

    def get(self, agent_id: int) -> Brain:
        try:
            return self._brains[agent_id]
        except KeyError as error:
            raise KeyError(f"agent {agent_id} has no registered brain") from error

    def name_for(self, agent_id: int) -> str:
        return self.get(agent_id).name

    def remove(self, agent_id: int) -> None:
        del self._brains[agent_id]


class BrainSystem:
    """Give each decision an independent RNG derived from tick and agent ID."""

    _RNG_NAMESPACE = 0xB4A1

    def __init__(self, registry: BrainRegistry, seed: int) -> None:
        self._registry = registry
        self._seed = seed

    def decide_all(
        self, observations: tuple[Observation, ...]
    ) -> tuple[ActionIntent, ...]:
        intents: list[ActionIntent] = []
        for observation in sorted(observations, key=lambda item: item.self_state.id):
            agent_id = observation.self_state.id
            rng = np.random.default_rng(
                np.random.SeedSequence(
                    [self._seed, self._RNG_NAMESPACE, observation.tick, agent_id]
                )
            )
            context = DecisionContext(tick=observation.tick, rng=rng)
            intent = self._registry.get(agent_id).decide(observation, context)
            if intent.agent_id != agent_id:
                raise ValueError(
                    f"brain for agent {agent_id} returned intent for {intent.agent_id}"
                )
            intents.append(intent)
        return tuple(intents)
