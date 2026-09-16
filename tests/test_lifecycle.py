from dataclasses import dataclass
from math import isnan

import numpy as np
import pytest

from agentic_simulation.actions import (
    ActionIntent,
    ActionResolver,
    ActionStatus,
    ActionType,
)
from agentic_simulation.agents import Observation
from agentic_simulation.agents.genome import Genome
from agentic_simulation.brains import DecisionContext
from agentic_simulation.config import AgentsConfig, AppConfig, LifeConfig, WorldConfig
from agentic_simulation.simulation import SimulationBuilder, SimulationEngine
from agentic_simulation.world import Position, ResourceType


@dataclass(frozen=True)
class FixedBrain:
    action: ActionType = ActionType.IDLE
    name: str = "genetic"

    def decide(
        self, observation: Observation, context: DecisionContext
    ) -> ActionIntent:
        return ActionIntent(observation.self_state.id, self.action)


def empty(life: LifeConfig) -> SimulationEngine:
    return SimulationBuilder.build(
        AppConfig(
            world=WorldConfig(seed=31, width=16, height=16),
            agents=AgentsConfig(initial_population=0),
            life=life,
        )
    )


def add(
    sim: SimulationEngine,
    energy: float = 100,
    action: ActionType = ActionType.IDLE,
    position: Position | None = None,
) -> int:
    agent = sim.agents.create(
        position or sim.world.traversable_positions()[0],
        health=100,
        energy=energy,
        vision_radius=3,
    )
    sim.spatial_index.add(agent.id, agent.position)
    sim.brains.register(agent.id, FixedBrain(action))
    return agent.id


def test_starvation_removes_agent_before_decisions_and_reuses_slot_not_id() -> None:
    sim = empty(LifeConfig(metabolism_cost=1))
    dead = add(sim, energy=0.5)
    report = sim.advance()
    assert report.observations == ()
    assert len(sim.agents) == 0
    assert report.life_events[0].reason == "starvation"
    assert sim.lifecycle.deaths == 1
    with pytest.raises(KeyError):
        sim.brains.get(dead)
    with pytest.raises(KeyError):
        sim.spatial_index.position_of(dead)
    with pytest.raises(KeyError):
        sim.agents.get(dead)
    new_id = add(sim)
    assert new_id > dead
    assert sim.agents.get(new_id).age == 0
    assert sim.agents._size == 1  # bounded storage under churn
    sim.advance()
    assert sim.agents.get(new_id).age == 1


def test_old_age_and_extinction_keep_simulation_and_metrics_valid() -> None:
    sim = empty(LifeConfig(max_age=2, metabolism_cost=0))
    add(sim)
    sim.advance()
    report = sim.advance()
    assert report.life_events[0].reason == "old_age"
    for _ in range(3):
        sim.advance()
    stats = sim.visualization_update()["agent_statistics"]
    assert stats["population"] == 0
    assert stats["genetic_diversity"] == 0
    assert not any(isnan(value) for value in stats["mean_genome"].values())


def test_food_contention_and_energy_cap_conserve_resource() -> None:
    sim = empty(LifeConfig(metabolism_cost=0))
    food = next(
        r for r in sim.world.resources() if r.resource_type is ResourceType.FOOD
    )
    sim.world.extract_resource(food.id, food.amount - 1)
    first = add(sim, energy=159, position=food.position)
    second = add(sim, energy=100, position=food.position)
    resolver = ActionResolver(
        sim.world, sim.agents, sim.spatial_index, sim.config.agents, sim.config.life
    )
    results = resolver.resolve_all(
        tuple(
            ActionIntent(i, ActionType.EAT, target_resource=food.id)
            for i in (second, first)
        ),
        0,
    )
    assert sum(r.resource_amount for r in results) == pytest.approx(1)
    assert sum(r.energy_gained for r in results) == pytest.approx(5)
    assert sim.agents.get(first).energy == 160
    assert sim.agents.get(second).energy == pytest.approx(104)
    assert sim.world.cell_at(food.position).resources[0].amount >= 0
    retry = resolver.resolve_all(
        (ActionIntent(second, ActionType.EAT, target_resource=food.id),), 0
    )[0]
    assert retry.status is ActionStatus.REJECTED


def test_reproduction_conserves_energy_and_defers_child_decisions() -> None:
    sim = empty(
        LifeConfig(
            metabolism_cost=0,
            reproduction_min_age=1,
            mutation_probability=1,
            mutation_sigma=0.2,
        )
    )
    parent = add(sim, action=ActionType.REPRODUCE)
    report = sim.advance()
    born = report.life_events[0]
    child = sim.agents.get(born.agent_id)
    assert born.event_type == "agent_born"
    assert child.id != parent and child.parent_id == parent
    assert child.generation == 1 and child.age == 0
    assert [o.self_state.id for o in report.observations] == [parent]
    assert sim.agents.get(parent).energy + child.energy == 90
    assert child.genome != sim.agents.get(parent).genome
    assert all(0 <= v <= 1 for v in child.genome.values())
    report2 = sim.advance()
    assert child.id in [o.self_state.id for o in report2.observations]
    assert report2.action_results[0].reason == "reproduction cooldown"


def test_population_cap_reserves_births_without_charging_rejected_parent() -> None:
    sim = empty(LifeConfig(metabolism_cost=0, reproduction_min_age=1, max_population=3))
    first = add(sim, action=ActionType.REPRODUCE)
    second = add(sim, action=ActionType.REPRODUCE)
    report = sim.advance()
    assert len(sim.agents) == 3
    assert sim.agents.get(first).energy == 40
    assert sim.agents.get(second).energy == 100
    assert report.action_results[1].reason == "population cap reached"


def test_reproduction_cannot_spend_parents_last_energy() -> None:
    sim = empty(LifeConfig(metabolism_cost=0, reproduction_min_age=1))
    parent = add(sim, energy=60, action=ActionType.REPRODUCE)
    result = sim.advance().action_results[0]
    assert result.status is ActionStatus.REJECTED
    assert sim.agents.get(parent).energy == 60
    assert len(sim.agents) == 1


def test_mutation_is_seeded_bounded_and_zero_probability_copies_exactly() -> None:
    gene = Genome()
    assert gene.mutate(np.random.default_rng(1), 0, 1) == gene
    assert gene.mutate(np.random.default_rng(1), 1, 0.4) == gene.mutate(
        np.random.default_rng(1), 1, 0.4
    )
    for seed in range(20):
        assert all(
            0 <= v <= 1 for v in gene.mutate(np.random.default_rng(seed), 1, 5).values()
        )
    with pytest.raises(ValueError):
        Genome(metabolism=float("nan"))


def test_birth_and_death_sequence_is_reproducible() -> None:
    config = LifeConfig(
        metabolism_cost=0, reproduction_min_age=1, max_age=4, reproduction_cooldown=2
    )
    first, second = empty(config), empty(config)
    for sim in (first, second):
        add(sim, action=ActionType.REPRODUCE)
    for _ in range(10):
        assert first.advance() == second.advance()
        assert first.state_hash() == second.state_hash()
    assert first.lifecycle.births > 0 and first.lifecycle.deaths > 0


def test_disabling_lifecycle_preserves_movement_only_mode() -> None:
    sim = empty(LifeConfig(enabled=False, max_age=1, metabolism_cost=100))
    agent_id = add(sim, energy=1)
    for _ in range(3):
        assert sim.advance().life_events == ()
    assert sim.agents.get(agent_id).energy == 1


def test_invalid_lifecycle_configuration_is_rejected() -> None:
    with pytest.raises(ValueError):
        LifeConfig(max_energy=60)
    with pytest.raises(ValueError):
        LifeConfig(food_energy=float("inf"))
    with pytest.raises(ValueError):
        AppConfig(life=LifeConfig(max_population=10))
