"""Deterministic updates for dense environmental layers."""

from __future__ import annotations

from math import pi, sin

import numpy as np

from agentic_simulation.config import WorldConfig
from agentic_simulation.world.models import EnvironmentLayers


class EnvironmentSystem:
    """Update climate fields in a fixed, documented order."""

    def __init__(self, config: WorldConfig) -> None:
        self._day_length = config.day_length_ticks
        self._amplitude = config.diurnal_temperature_amplitude

    def update(self, layers: EnvironmentLayers, tick: int) -> None:
        phase = 2.0 * pi * (tick % self._day_length) / self._day_length
        offset = self._amplitude * sin(phase)
        np.add(layers.base_temperature, offset, out=layers.temperature)
