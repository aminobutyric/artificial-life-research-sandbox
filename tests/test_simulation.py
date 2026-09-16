from agentic_simulation.actions import ActionStatus, ActionType
from agentic_simulation.config import (
    AgentsConfig,
    AppConfig,
    ResourceConfig,
    ResourcesConfig,
    WorldConfig,
)
from agentic_simulation.simulation import SimulationBuilder


def small_simulation_config(seed: int = 29) -> AppConfig:
    return AppConfig(
        world=WorldConfig(
            seed=seed,
            width=32,
            height=24,
            resources=ResourcesConfig(
                food=ResourceConfig(initial_nodes=20, capacity=20),
                water=ResourceConfig(initial_nodes=8, capacity=30),
            ),
        ),
        agents=AgentsConfig(
            initial_population=40,
            initial_energy=25.0,
            vision_radius=2.0,
            move_energy_cost=0.2,
            idle_probability=0.2,
        ),
    )


def test_simulation_is_deterministic_and_respects_boundaries() -> None:
    first = SimulationBuilder.build(small_simulation_config())
    second = SimulationBuilder.build(small_simulation_config())

    for expected_tick in range(1, 31):
        first_report = first.advance()
        second_report = second.advance()
        assert first_report == second_report
        assert first_report.tick == expected_tick
        assert len(first_report.observations) == 40
        assert len(first_report.intents) == 40
        assert len(first_report.action_results) == 40

    assert first.state_hash() == second.state_hash()
    assert SimulationBuilder.build(small_simulation_config(30)).state_hash() != (
        first.state_hash()
    )
    assert all(
        first.world.cell_at(agent.position).traversable
        for agent in first.agents.living()
    )
    assert all(agent.age == 30 for agent in first.agents.living())


def test_random_brains_produce_only_idle_or_successful_local_moves() -> None:
    simulation = SimulationBuilder.build(small_simulation_config())
    report = simulation.advance()

    assert {intent.action_type for intent in report.intents} <= {
        ActionType.IDLE,
        ActionType.MOVE,
    }
    assert all(
        result.status is ActionStatus.SUCCEEDED for result in report.action_results
    )
    assert all(
        abs(result.destination.x - result.origin.x)
        + abs(result.destination.y - result.origin.y)
        in {0, 1}
        for result in report.action_results
    )


def test_visualization_includes_agents_in_full_and_compact_frames() -> None:
    simulation = SimulationBuilder.build(small_simulation_config())
    frame = simulation.visualization_frame()
    assert len(frame["agents"]) == 40
    assert frame["agent_statistics"]["population"] == 40

    simulation.advance()
    update = simulation.visualization_update()
    assert update["kind"] == "update"
    assert len(update["agents"]) == 40
    assert "layers" not in update
