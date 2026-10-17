"""Explicit core codecs. New memory cores add a versioned payload and decoder here."""

from typing import Any

from pydantic import Field

from agentic_simulation.config import LifeConfig, StrictModel

from .base import Brain
from .genetic import GeneticBrain
from .random import RandomBrain


class RandomState(StrictModel):
    idle_probability: float = Field(ge=0, le=1)


class GeneticState(StrictModel):
    life: LifeConfig


def dump_brain(brain: Brain) -> dict[str, Any]:
    if type(brain) not in (RandomBrain, GeneticBrain):
        raise ValueError(f"unsupported checkpoint core: {type(brain).__name__}")
    assert isinstance(brain, RandomBrain | GeneticBrain)
    return {
        "core": brain.name,
        "state_version": brain.state_version,
        "state": brain.checkpoint_state(),
    }


def restore_brain(core: str, state_version: int, state: dict[str, Any]) -> Brain:
    if state_version != 1:
        raise ValueError(f"unsupported {core} brain-state version: {state_version}")
    if core == "random":
        return RandomBrain(RandomState.model_validate(state).idle_probability)
    if core == "genetic":
        return GeneticBrain(GeneticState.model_validate(state).life)
    raise ValueError(f"unsupported checkpoint core: {core}")
