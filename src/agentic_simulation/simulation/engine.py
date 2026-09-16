"""Deterministic orchestration across world, agents, brains, and actions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from agentic_simulation.actions import ActionIntent, ActionResolver, ActionResult
from agentic_simulation.agents import (
    AgentSpatialIndex,
    AgentStore,
    Observation,
    PerceptionSystem,
    spawn_initial_population,
)
from agentic_simulation.brains import BrainRegistry, BrainSystem, RandomBrain
from agentic_simulation.brains.genetic import GeneticBrain
from agentic_simulation.config import AppConfig
from agentic_simulation.world import World, WorldBuilder, WorldEvent

from .lifecycle import Lifecycle, LifeEvent


@dataclass(frozen=True, slots=True)
class TickReport:
    tick: int
    observations: tuple[Observation, ...]
    intents: tuple[ActionIntent, ...]
    action_results: tuple[ActionResult, ...]
    world_events: tuple[WorldEvent, ...]
    life_events: tuple[LifeEvent, ...] = ()


class SimulationEngine:
    """Advance all systems in one stable, explicit order."""

    def __init__(
        self,
        config: AppConfig,
        world: World,
        agents: AgentStore,
        spatial_index: AgentSpatialIndex,
        brains: BrainRegistry,
        brain_system: BrainSystem,
    ) -> None:
        self.config = config
        self.world = world
        self.agents = agents
        self.spatial_index = spatial_index
        self.brains = brains
        self._perception = PerceptionSystem(world, agents, spatial_index)
        self._brain_system = brain_system
        self._resolver = ActionResolver(
            world, agents, spatial_index, config.agents, config.life
        )
        self.lifecycle = Lifecycle(config, agents, spatial_index, brains)

    @property
    def tick(self) -> int:
        return self.world.tick

    def advance(self) -> TickReport:
        world_events = self.world.advance()
        self.agents.age_all()
        life_events = self.lifecycle.before_decisions(self.tick)
        observations = self._perception.observe_all()
        intents = self._brain_system.decide_all(observations)
        results = self._resolver.resolve_all(intents, self.tick)
        life_events += self.lifecycle.births_after_actions(results, self.tick)
        life_events += self.lifecycle.remove_dead(self.tick)
        return TickReport(
            tick=self.tick,
            observations=observations,
            intents=intents,
            action_results=results,
            world_events=world_events,
            life_events=life_events,
        )

    def state_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.config.agents.model_dump_json().encode())
        digest.update(self.config.life.model_dump_json().encode())
        digest.update(str((self.lifecycle.births, self.lifecycle.deaths)).encode())
        digest.update(self.world.state_hash().encode())
        digest.update(self.agents.state_hash().encode())
        return digest.hexdigest()

    def visualization_frame(self) -> dict[str, Any]:
        frame = self.world.visualization_frame()
        frame["agents"] = self._agent_payload()
        frame["agent_statistics"] = self._agent_statistics()
        return frame

    def visualization_update(self) -> dict[str, Any]:
        update = self.world.visualization_update()
        update["agents"] = self._agent_payload()
        update["agent_statistics"] = self._agent_statistics()
        return update

    def _agent_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "id": agent.id,
                "x": agent.position.x,
                "y": agent.position.y,
                "health": round(agent.health, 3),
                "energy": round(agent.energy, 3),
                "age": agent.age,
                "brain": self.brains.name_for(agent.id),
                "generation": agent.generation,
                "parent_id": agent.parent_id,
                "genome": dict(
                    zip(
                        (
                            "exploration",
                            "food_attraction",
                            "reproduction_threshold",
                            "metabolism",
                            "vision",
                        ),
                        agent.genome.values(),
                        strict=True,
                    )
                ),
            }
            for agent in self.agents.living()
        ]

    def _agent_statistics(self) -> dict[str, Any]:
        living = self.agents.living()
        population = len(living)
        traits = np.array([a.genome.values() for a in living], dtype=np.float64)
        evolution = {
            "births": self.lifecycle.births,
            "deaths": self.lifecycle.deaths,
            "max_generation": max((a.generation for a in living), default=0),
            "genetic_diversity": round(float(traits.std(axis=0).mean()), 4)
            if population
            else 0.0,
            "mean_genome": dict(
                zip(
                    (
                        "exploration",
                        "food_attraction",
                        "reproduction_threshold",
                        "metabolism",
                        "vision",
                    ),
                    map(float, traits.mean(axis=0)) if population else [0.0] * 5,
                    strict=True,
                )
            ),
        }
        if not population:
            return {
                "population": 0,
                "average_energy": 0.0,
                "average_age": 0.0,
                "brain_types": {},
                **evolution,
            }
        brain_types: dict[str, int] = {}
        for agent in living:
            name = self.brains.name_for(agent.id)
            brain_types[name] = brain_types.get(name, 0) + 1
        return {
            "population": population,
            "average_energy": round(
                sum(agent.energy for agent in living) / population, 3
            ),
            "average_age": round(sum(agent.age for agent in living) / population, 3),
            "brain_types": brain_types,
            **evolution,
        }


class SimulationBuilder:
    _SPAWN_NAMESPACE = 0xA63E
    _BRAIN_NAMESPACE = 0xB4A1

    @classmethod
    def build(cls, config: AppConfig) -> SimulationEngine:
        world = WorldBuilder.build(config.world)
        spawn_rng = np.random.default_rng(
            np.random.SeedSequence([config.world.seed, cls._SPAWN_NAMESPACE])
        )
        agents, spatial_index = spawn_initial_population(
            world, config.agents, spawn_rng
        )
        registry = BrainRegistry()
        for agent in agents.living():
            registry.register(
                agent.id,
                GeneticBrain(config.life)
                if config.agents.brain == "genetic"
                else RandomBrain(config.agents.idle_probability),
            )
        brain_seed = config.world.seed ^ cls._BRAIN_NAMESPACE
        return SimulationEngine(
            config,
            world,
            agents,
            spatial_index,
            registry,
            BrainSystem(registry, brain_seed),
        )
