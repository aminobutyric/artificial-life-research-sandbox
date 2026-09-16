"""An interpretable foraging policy using only local observations."""

from collections import deque

from agentic_simulation.actions import ActionIntent, ActionType
from agentic_simulation.agents import Observation
from agentic_simulation.config import LifeConfig
from agentic_simulation.world import Position, ResourceType

from .base import DecisionContext


class GeneticBrain:
    def __init__(self, life: LifeConfig) -> None:
        self._life = life

    @property
    def name(self) -> str:
        return "genetic"

    def decide(
        self, observation: Observation, context: DecisionContext
    ) -> ActionIntent:
        agent = observation.self_state
        gene = agent.genome
        life = self._life
        cost = life.child_energy + life.reproduction_cost
        threshold = cost + (life.max_energy - cost) * (
            0.1 + 0.9 * gene.reproduction_threshold
        )
        ready = (
            agent.last_reproduction < 0
            or context.tick - agent.last_reproduction >= life.reproduction_cooldown
        )
        if (
            life.enabled
            and ready
            and agent.age >= life.reproduction_min_age
            and agent.energy >= threshold
        ):
            return ActionIntent(agent.id, ActionType.REPRODUCE)
        food = tuple(
            r
            for r in observation.nearby_resources
            if r.resource_type is ResourceType.FOOD and r.amount > 0
        )
        for resource in food:
            if (
                life.enabled
                and resource.position == agent.position
                and agent.energy < life.max_energy
            ):
                return ActionIntent(
                    agent.id, ActionType.EAT, target_resource=resource.id
                )

        # Breadth-first paths use only observed traversable cells, including obstacles.
        cells = {c.position for c in observation.nearby_cells if c.traversable}
        paths: dict[Position, tuple[int, Position]] = {
            agent.position: (0, agent.position)
        }
        queue = deque([agent.position])
        while queue:
            current = queue.popleft()
            distance, first = paths[current]
            for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
                neighbor = Position(current.x + dx, current.y + dy)
                if neighbor in cells and neighbor not in paths:
                    paths[neighbor] = (
                        distance + 1,
                        neighbor if distance == 0 else first,
                    )
                    queue.append(neighbor)
        reachable = [
            r for r in food if r.position in paths and r.position != agent.position
        ]
        hunger = max(0.0, 1 - agent.energy / life.max_energy)
        if reachable and context.rng.random() < 0.5 + 0.5 * max(
            hunger, gene.food_attraction
        ):
            target = min(reachable, key=lambda r: (paths[r.position][0], r.id))
            return ActionIntent(agent.id, ActionType.MOVE, paths[target.position][1])
        adjacent = sorted(p for p, (d, _) in paths.items() if d == 1)
        if adjacent and context.rng.random() < 0.25 + 0.75 * gene.exploration:
            return ActionIntent(
                agent.id,
                ActionType.MOVE,
                adjacent[int(context.rng.integers(len(adjacent)))],
            )
        return ActionIntent(agent.id, ActionType.IDLE)
