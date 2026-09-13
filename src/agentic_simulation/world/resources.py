"""Sparse resource storage with stable IDs and deterministic iteration."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor, hypot

from agentic_simulation.world.models import (
    Position,
    ResourceExtraction,
    ResourceType,
    ResourceView,
)


@dataclass(slots=True)
class _ResourceNode:
    id: int
    resource_type: ResourceType
    position: Position
    amount: float
    capacity: float
    regeneration_rate: float

    def view(self) -> ResourceView:
        return ResourceView(
            id=self.id,
            resource_type=self.resource_type,
            position=self.position,
            amount=self.amount,
            capacity=self.capacity,
            regeneration_rate=self.regeneration_rate,
        )


class ResourceStore:
    """Own mutable resources while exposing immutable views."""

    def __init__(self, *, next_id: int = 1) -> None:
        self._nodes: dict[int, _ResourceNode] = {}
        self._by_cell: dict[Position, dict[ResourceType, int]] = {}
        self._next_id = next_id

    @property
    def next_id(self) -> int:
        return self._next_id

    def add(
        self,
        resource_type: ResourceType,
        position: Position,
        amount: float,
        capacity: float,
        regeneration_rate: float,
        *,
        resource_id: int | None = None,
    ) -> ResourceView:
        if capacity <= 0.0:
            raise ValueError("resource capacity must be positive")
        if not 0.0 <= amount <= capacity:
            raise ValueError("resource amount must be between zero and capacity")
        if regeneration_rate < 0.0:
            raise ValueError("regeneration rate cannot be negative")

        existing_cell = self._by_cell.get(position, {})
        if resource_type in existing_cell:
            raise ValueError(f"{resource_type} already exists at {position}")

        node_id = self._next_id if resource_id is None else resource_id
        if node_id in self._nodes:
            raise ValueError(f"resource ID {node_id} already exists")
        if node_id < 1:
            raise ValueError("resource IDs must be positive")

        cell = self._by_cell.setdefault(position, {})
        self._next_id = max(self._next_id, node_id + 1)
        node = _ResourceNode(
            id=node_id,
            resource_type=resource_type,
            position=position,
            amount=float(amount),
            capacity=float(capacity),
            regeneration_rate=float(regeneration_rate),
        )
        self._nodes[node_id] = node
        cell[resource_type] = node_id
        return node.view()

    def get(self, resource_id: int) -> ResourceView:
        try:
            return self._nodes[resource_id].view()
        except KeyError as error:
            raise KeyError(f"unknown resource ID {resource_id}") from error

    def all(self) -> tuple[ResourceView, ...]:
        return tuple(self._nodes[node_id].view() for node_id in sorted(self._nodes))

    def at(self, position: Position) -> tuple[ResourceView, ...]:
        ids = self._by_cell.get(position, {}).values()
        return tuple(self._nodes[node_id].view() for node_id in sorted(ids))

    def near(
        self,
        center: Position,
        radius: float,
        resource_type: ResourceType | None = None,
    ) -> tuple[ResourceView, ...]:
        if radius < 0.0:
            raise ValueError("radius cannot be negative")
        result: list[ResourceView] = []
        for y in range(floor(center.y - radius), ceil(center.y + radius) + 1):
            for x in range(floor(center.x - radius), ceil(center.x + radius) + 1):
                if hypot(x - center.x, y - center.y) > radius:
                    continue
                for node_id in self._by_cell.get(Position(x, y), {}).values():
                    node = self._nodes[node_id]
                    if resource_type is None or node.resource_type is resource_type:
                        result.append(node.view())
        return tuple(sorted(result, key=lambda node: node.id))

    def extract(self, resource_id: int, requested: float) -> ResourceExtraction:
        if requested < 0.0:
            raise ValueError("requested resource amount cannot be negative")
        try:
            node = self._nodes[resource_id]
        except KeyError as error:
            raise KeyError(f"unknown resource ID {resource_id}") from error
        extracted = min(float(requested), node.amount)
        node.amount -= extracted
        return ResourceExtraction(resource_id, float(requested), extracted, node.amount)

    def regenerate(self) -> tuple[int, float]:
        changed = 0
        total = 0.0
        for node_id in sorted(self._nodes):
            node = self._nodes[node_id]
            increase = min(node.regeneration_rate, node.capacity - node.amount)
            if increase > 0.0:
                node.amount += increase
                changed += 1
                total += increase
        return changed, total
