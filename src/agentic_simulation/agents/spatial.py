"""Deterministic cell index for local agent queries."""

from __future__ import annotations

from math import ceil, floor, hypot

from agentic_simulation.world import Position


class AgentSpatialIndex:
    def __init__(self) -> None:
        self._by_cell: dict[Position, set[int]] = {}
        self._positions: dict[int, Position] = {}

    def add(self, agent_id: int, position: Position) -> None:
        if agent_id in self._positions:
            raise ValueError(f"agent {agent_id} is already indexed")
        self._positions[agent_id] = position
        self._by_cell.setdefault(position, set()).add(agent_id)

    def move(self, agent_id: int, destination: Position) -> None:
        try:
            origin = self._positions[agent_id]
        except KeyError as error:
            raise KeyError(f"agent {agent_id} is not indexed") from error
        if origin == destination:
            return
        origin_ids = self._by_cell[origin]
        origin_ids.remove(agent_id)
        if not origin_ids:
            del self._by_cell[origin]
        self._positions[agent_id] = destination
        self._by_cell.setdefault(destination, set()).add(agent_id)

    def at(self, position: Position) -> tuple[int, ...]:
        return tuple(sorted(self._by_cell.get(position, ())))

    def remove(self, agent_id: int) -> None:
        position = self._positions.pop(agent_id)
        self._by_cell[position].remove(agent_id)
        if not self._by_cell[position]:
            del self._by_cell[position]

    def near(
        self,
        center: Position,
        radius: float,
        *,
        exclude_id: int | None = None,
    ) -> tuple[int, ...]:
        if radius < 0.0:
            raise ValueError("radius cannot be negative")
        result: list[int] = []
        for y in range(floor(center.y - radius), ceil(center.y + radius) + 1):
            for x in range(floor(center.x - radius), ceil(center.x + radius) + 1):
                if hypot(x - center.x, y - center.y) > radius:
                    continue
                for agent_id in self._by_cell.get(Position(x, y), ()):
                    if agent_id != exclude_id:
                        result.append(agent_id)
        return tuple(sorted(result))

    def position_of(self, agent_id: int) -> Position:
        try:
            return self._positions[agent_id]
        except KeyError as error:
            raise KeyError(f"agent {agent_id} is not indexed") from error
