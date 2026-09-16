"""Common intelligence contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from agentic_simulation.actions import ActionIntent
from agentic_simulation.agents import Observation


@dataclass(frozen=True, slots=True)
class DecisionContext:
    tick: int
    rng: np.random.Generator


class Brain(Protocol):
    @property
    def name(self) -> str: ...

    def decide(
        self, observation: Observation, context: DecisionContext
    ) -> ActionIntent: ...
