"""Agent requests and centrally resolved outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentic_simulation.world import Position


class ActionType(StrEnum):
    IDLE = "idle"
    MOVE = "move"
    EAT = "eat"
    REPRODUCE = "reproduce"


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ActionIntent:
    agent_id: int
    action_type: ActionType
    target_position: Position | None = None
    target_resource: int | None = None


@dataclass(frozen=True, slots=True)
class ActionResult:
    tick: int
    agent_id: int
    action_type: ActionType
    status: ActionStatus
    origin: Position
    destination: Position
    energy_cost: float
    reason: str | None = None
    resource_amount: float = 0.0
    energy_gained: float = 0.0
