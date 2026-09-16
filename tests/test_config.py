from pathlib import Path

import pytest
from pydantic import ValidationError

from agentic_simulation.config import GenerationConfig, load_config


def test_load_example_config() -> None:
    config = load_config(Path("experiments/world.yaml"))

    assert config.world.width == 100
    assert config.world.seed == 42
    assert config.agents.initial_population == 500
    assert config.visualization.ticks_per_second == 8.0


def test_generation_levels_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="water_level must be lower"):
        GenerationConfig(water_level=0.8, mountain_level=0.2)
