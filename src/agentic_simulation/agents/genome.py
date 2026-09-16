"""Small immutable genomes; every gene is in [0, 1]."""

from __future__ import annotations

from dataclasses import dataclass, fields
from math import isfinite

import numpy as np


@dataclass(frozen=True, slots=True)
class Genome:
    exploration: float = 0.5
    food_attraction: float = 0.5
    reproduction_threshold: float = 0.5
    metabolism: float = 0.5
    vision: float = 0.5

    def __post_init__(self) -> None:
        if any(not isfinite(v) or not 0 <= v <= 1 for v in self.values()):
            raise ValueError("genes must be finite values in [0, 1]")

    def values(self) -> tuple[float, ...]:
        return tuple(getattr(self, field.name) for field in fields(self))

    @classmethod
    def random(cls, rng: np.random.Generator) -> Genome:
        return cls(*map(float, rng.random(5)))

    def mutate(
        self, rng: np.random.Generator, probability: float, sigma: float
    ) -> Genome:
        if not 0 <= probability <= 1 or not isfinite(sigma) or sigma < 0:
            raise ValueError("invalid mutation parameters")
        return Genome(
            *(
                float(np.clip(v + rng.normal(0, sigma), 0, 1))
                if rng.random() < probability
                else v
                for v in self.values()
            )
        )

    def vision_radius(self, base: float) -> float:
        return max(1.0, base * (0.5 + self.vision))
