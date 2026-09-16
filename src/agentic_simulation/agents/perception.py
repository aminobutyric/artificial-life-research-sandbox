"""Build partial, immutable observations for agents."""

from __future__ import annotations

from agentic_simulation.world import World

from .models import NearbyAgentObservation, Observation, SelfObservation
from .spatial import AgentSpatialIndex
from .store import AgentStore


class PerceptionSystem:
    def __init__(
        self,
        world: World,
        agents: AgentStore,
        spatial_index: AgentSpatialIndex,
    ) -> None:
        self._world = world
        self._agents = agents
        self._spatial_index = spatial_index

    def observe_all(self) -> tuple[Observation, ...]:
        return tuple(self.observe(agent.id) for agent in self._agents.living())

    def observe(self, agent_id: int) -> Observation:
        agent = self._agents.get(agent_id)
        region = self._world.observe_region(agent.position, agent.vision_radius)
        nearby_ids = self._spatial_index.near(
            agent.position, agent.vision_radius, exclude_id=agent.id
        )
        nearby_agents = tuple(
            NearbyAgentObservation(id=other.id, position=other.position)
            for other in (self._agents.get(other_id) for other_id in nearby_ids)
            if other.alive
        )
        return Observation(
            self_state=SelfObservation(
                id=agent.id,
                position=agent.position,
                health=agent.health,
                energy=agent.energy,
                age=agent.age,
                genome=agent.genome,
                generation=agent.generation,
                last_reproduction=agent.last_reproduction,
            ),
            nearby_agents=nearby_agents,
            nearby_resources=region.resources,
            nearby_cells=region.cells,
            tick=self._world.tick,
        )
