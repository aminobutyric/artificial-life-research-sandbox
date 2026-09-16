from agentic_simulation.actions import (
    ActionIntent,
    ActionResolver,
    ActionStatus,
    ActionType,
)
from agentic_simulation.config import AgentsConfig, AppConfig, WorldConfig
from agentic_simulation.simulation import SimulationBuilder
from agentic_simulation.world import Position


def test_world_rejects_invalid_brain_intent_without_changing_state() -> None:
    config = AppConfig(
        world=WorldConfig(seed=5, width=20, height=20),
        agents=AgentsConfig(initial_population=1),
    )
    simulation = SimulationBuilder.build(config)
    agent = simulation.agents.get(1)
    resolver = ActionResolver(
        simulation.world,
        simulation.agents,
        simulation.spatial_index,
        config.agents,
    )
    intent = ActionIntent(
        agent_id=agent.id,
        action_type=ActionType.MOVE,
        target_position=Position(agent.position.x + 10, agent.position.y + 10),
    )

    result = resolver.resolve_all((intent,), tick=1)[0]

    assert result.status is ActionStatus.REJECTED
    assert result.reason == "move must target an adjacent cell"
    assert simulation.agents.get(agent.id) == agent
    assert simulation.spatial_index.position_of(agent.id) == agent.position


def test_duplicate_intents_are_rejected_deterministically() -> None:
    config = AppConfig(
        world=WorldConfig(seed=6, width=20, height=20),
        agents=AgentsConfig(initial_population=1),
    )
    simulation = SimulationBuilder.build(config)
    resolver = ActionResolver(
        simulation.world,
        simulation.agents,
        simulation.spatial_index,
        config.agents,
    )
    idle = ActionIntent(agent_id=1, action_type=ActionType.IDLE)

    first, second = resolver.resolve_all((idle, idle), tick=1)

    assert first.status is ActionStatus.SUCCEEDED
    assert second.status is ActionStatus.REJECTED
    assert second.reason == "duplicate intent for this tick"
