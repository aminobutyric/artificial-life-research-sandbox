"""Survival and deferred births; no persistent corpse or brain accumulation."""

from dataclasses import dataclass

import numpy as np

from agentic_simulation.actions import ActionResult, ActionStatus, ActionType
from agentic_simulation.agents import AgentSpatialIndex, AgentStore
from agentic_simulation.brains import BrainRegistry, RandomBrain
from agentic_simulation.brains.genetic import GeneticBrain
from agentic_simulation.config import AppConfig
from agentic_simulation.world import Position


@dataclass(frozen=True, slots=True)
class LifeEvent:
    tick: int
    event_type: str
    agent_id: int
    position: Position
    reason: str = ""
    parent_id: int | None = None
    generation: int = 0
    genome: tuple[float, ...] = ()
    energy: float = 0.0
    schema_version: int = 1


class Lifecycle:
    def __init__(
        self,
        config: AppConfig,
        agents: AgentStore,
        spatial: AgentSpatialIndex,
        brains: BrainRegistry,
    ) -> None:
        self.config = config
        self.agents = agents
        self.spatial = spatial
        self.brains = brains
        self.births = 0
        self.deaths = 0

    def before_decisions(self, tick: int) -> tuple[LifeEvent, ...]:
        if not self.config.life.enabled:
            return ()
        for agent in self.agents.living():
            cost = self.config.life.metabolism_cost * (0.5 + agent.genome.metabolism)
            self.agents.spend(agent.id, min(cost, agent.energy))
        return self.remove_dead(tick)

    def remove_dead(self, tick: int) -> tuple[LifeEvent, ...]:
        if not self.config.life.enabled:
            return ()
        events = []
        for agent in self.agents.living():
            reason = (
                "health"
                if agent.health <= 0
                else "starvation"
                if agent.energy <= 0
                else "old_age"
                if agent.age >= self.config.life.max_age
                else ""
            )
            if reason:
                events.append(
                    LifeEvent(
                        tick,
                        "agent_died",
                        agent.id,
                        agent.position,
                        reason,
                        agent.parent_id,
                        agent.generation,
                        agent.genome.values(),
                        agent.energy,
                    )
                )
                self.spatial.remove(agent.id)
                self.brains.remove(agent.id)
                self.agents.remove(agent.id)
                self.deaths += 1
        return tuple(events)

    def births_after_actions(
        self, results: tuple[ActionResult, ...], tick: int
    ) -> tuple[LifeEvent, ...]:
        events = []
        for result in results:
            if (
                result.action_type is not ActionType.REPRODUCE
                or result.status is not ActionStatus.SUCCEEDED
            ):
                continue
            parent = self.agents.get(result.agent_id)
            rng = np.random.default_rng(
                np.random.SeedSequence(
                    [
                        self.config.world.seed,
                        0xC41D,
                        tick,
                        parent.id,
                        self.agents.next_id,
                    ]
                )
            )
            genome = parent.genome.mutate(
                rng,
                self.config.life.mutation_probability,
                self.config.life.mutation_sigma,
            )
            child = self.agents.create(
                parent.position,
                health=self.config.agents.initial_health,
                energy=self.config.life.child_energy,
                vision_radius=genome.vision_radius(self.config.agents.vision_radius),
                genome=genome,
                parent_id=parent.id,
                generation=parent.generation + 1,
            )
            self.spatial.add(child.id, child.position)
            self.brains.register(
                child.id,
                GeneticBrain(self.config.life)
                if self.brains.name_for(parent.id) == "genetic"
                else RandomBrain(self.config.agents.idle_probability),
            )
            events.append(
                LifeEvent(
                    tick,
                    "agent_born",
                    child.id,
                    child.position,
                    parent_id=parent.id,
                    generation=child.generation,
                    genome=genome.values(),
                    energy=child.energy,
                )
            )
            self.births += 1
        return tuple(events)
