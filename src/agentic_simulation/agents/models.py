"""Immutable agent and perception models."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_simulation.world import CellView, Position, ResourceView

from .genome import Genome


@dataclass(frozen=True, slots=True)
class AgentView:
    id: int
    position: Position
    health: float
    energy: float
    age: int
    vision_radius: float
    alive: bool
    genome: Genome = field(default_factory=Genome)
    generation: int = 0
    parent_id: int | None = None
    last_reproduction: int = -1


@dataclass(frozen=True, slots=True)
class SelfObservation:
    id: int
    position: Position
    health: float
    energy: float
    age: int
    genome: Genome = field(default_factory=Genome)
    generation: int = 0
    last_reproduction: int = -1


@dataclass(frozen=True, slots=True)
class NearbyAgentObservation:
    id: int
    position: Position


@dataclass(frozen=True, slots=True)
class Observation:
    self_state: SelfObservation
    nearby_agents: tuple[NearbyAgentObservation, ...]
    nearby_resources: tuple[ResourceView, ...]
    nearby_cells: tuple[CellView, ...]
    tick: int
