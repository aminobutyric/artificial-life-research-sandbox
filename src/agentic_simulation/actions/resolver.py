"""Validate intents and apply physical results centrally."""

from __future__ import annotations

from agentic_simulation.agents import AgentSpatialIndex, AgentStore
from agentic_simulation.config import AgentsConfig, LifeConfig
from agentic_simulation.world import Position, ResourceType, World

from .models import ActionIntent, ActionResult, ActionStatus, ActionType


class ActionResolver:
    def __init__(
        self,
        world: World,
        agents: AgentStore,
        spatial_index: AgentSpatialIndex,
        config: AgentsConfig,
        life: LifeConfig | None = None,
    ) -> None:
        self._world = world
        self._agents = agents
        self._spatial_index = spatial_index
        self._move_energy_cost = config.move_energy_cost
        self._life = life or LifeConfig(enabled=False)
        self._pending_births = 0

    def resolve_all(
        self, intents: tuple[ActionIntent, ...], tick: int
    ) -> tuple[ActionResult, ...]:
        results: list[ActionResult] = []
        self._pending_births = 0
        seen: set[int] = set()
        for intent in sorted(intents, key=lambda item: item.agent_id):
            if intent.agent_id in seen:
                results.append(
                    self._rejected(intent, tick, "duplicate intent for this tick")
                )
                continue
            seen.add(intent.agent_id)
            results.append(self._resolve(intent, tick))
        return tuple(results)

    def _resolve(self, intent: ActionIntent, tick: int) -> ActionResult:
        try:
            agent = self._agents.get(intent.agent_id)
        except KeyError:
            return self._rejected(intent, tick, "unknown agent")
        if not agent.alive:
            return self._rejected(intent, tick, "agent is not alive")
        if self._life.enabled and (agent.energy <= 0 or agent.health <= 0):
            return self._rejected(
                intent, tick, "agent cannot act with zero energy or health"
            )
        if intent.action_type is ActionType.EAT:
            return self._eat(intent, tick)
        if intent.action_type is ActionType.REPRODUCE:
            return self._reproduce(intent, tick)
        if intent.action_type is ActionType.IDLE:
            return ActionResult(
                tick=tick,
                agent_id=agent.id,
                action_type=ActionType.IDLE,
                status=ActionStatus.SUCCEEDED,
                origin=agent.position,
                destination=agent.position,
                energy_cost=0.0,
            )
        if intent.action_type is not ActionType.MOVE:
            return self._rejected(intent, tick, "unsupported action")
        return self._resolve_move(intent, tick)

    def _eat(self, intent: ActionIntent, tick: int) -> ActionResult:
        if not self._life.enabled:
            return self._rejected(intent, tick, "lifecycle is disabled")
        agent = self._agents.get(intent.agent_id)
        food = next(
            (
                r
                for r in self._world.cell_at(agent.position).resources
                if r.id == intent.target_resource
                and r.resource_type is ResourceType.FOOD
            ),
            None,
        )
        if food is None or food.amount <= 0:
            return self._rejected(intent, tick, "food is unavailable at this cell")
        conversion = self._life.food_energy * (0.75 + 0.5 * agent.genome.metabolism)
        requested = min(
            self._life.bite_size,
            max(0.0, self._life.max_energy - agent.energy) / conversion,
        )
        if requested <= 0:
            return self._rejected(intent, tick, "energy is already full")
        extraction = self._world.extract_resource(food.id, requested)
        gained = self._agents.feed(
            agent.id, extraction.extracted * conversion, self._life.max_energy
        )
        return ActionResult(
            tick,
            agent.id,
            ActionType.EAT,
            ActionStatus.SUCCEEDED,
            agent.position,
            agent.position,
            0.0,
            resource_amount=extraction.extracted,
            energy_gained=gained,
        )

    def _reproduce(self, intent: ActionIntent, tick: int) -> ActionResult:
        life = self._life
        if not life.enabled:
            return self._rejected(intent, tick, "lifecycle is disabled")
        agent = self._agents.get(intent.agent_id)
        if agent.age < life.reproduction_min_age:
            return self._rejected(intent, tick, "agent is too young")
        if (
            agent.last_reproduction >= 0
            and tick - agent.last_reproduction < life.reproduction_cooldown
        ):
            return self._rejected(intent, tick, "reproduction cooldown")
        if len(self._agents) + self._pending_births >= life.max_population:
            return self._rejected(intent, tick, "population cap reached")
        cost = life.child_energy + life.reproduction_cost
        if agent.energy <= cost:
            return self._rejected(
                intent, tick, "insufficient energy for parent and child"
            )
        self._agents.spend(agent.id, cost)
        self._agents.mark_reproduction(agent.id, tick)
        self._pending_births += 1
        return ActionResult(
            tick,
            agent.id,
            ActionType.REPRODUCE,
            ActionStatus.SUCCEEDED,
            agent.position,
            agent.position,
            cost,
        )

    def _resolve_move(self, intent: ActionIntent, tick: int) -> ActionResult:
        agent = self._agents.get(intent.agent_id)
        destination = intent.target_position
        if destination is None:
            return self._rejected(intent, tick, "move requires a destination")
        distance = abs(destination.x - agent.position.x) + abs(
            destination.y - agent.position.y
        )
        if distance != 1:
            return self._rejected(intent, tick, "move must target an adjacent cell")
        if not self._world.contains(destination):
            return self._rejected(intent, tick, "destination is outside the world")
        if not self._world.cell_at(destination).traversable:
            return self._rejected(intent, tick, "destination is not traversable")
        if agent.energy < self._move_energy_cost:
            return self._rejected(intent, tick, "insufficient energy")

        origin = agent.position
        moved = self._agents.move(agent.id, destination, self._move_energy_cost)
        self._spatial_index.move(agent.id, destination)
        return ActionResult(
            tick=tick,
            agent_id=agent.id,
            action_type=ActionType.MOVE,
            status=ActionStatus.SUCCEEDED,
            origin=origin,
            destination=moved.position,
            energy_cost=self._move_energy_cost,
        )

    def _rejected(self, intent: ActionIntent, tick: int, reason: str) -> ActionResult:
        try:
            position = self._agents.get(intent.agent_id).position
        except KeyError:
            position = Position(-1, -1)
        return ActionResult(
            tick=tick,
            agent_id=intent.agent_id,
            action_type=intent.action_type,
            status=ActionStatus.REJECTED,
            origin=position,
            destination=position,
            energy_cost=0.0,
            reason=reason,
        )
