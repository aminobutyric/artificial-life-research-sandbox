"""Array-oriented agent state with stable identities."""

from __future__ import annotations

import hashlib
import heapq
import json
from math import isfinite

import numpy as np
from numpy.typing import NDArray

from agentic_simulation.world import Position

from .genome import Genome
from .models import AgentView


class AgentStore:
    """Own mutable agent state while exposing immutable views."""

    def __init__(self, *, initial_capacity: int = 16) -> None:
        if initial_capacity < 0:
            raise ValueError("initial capacity cannot be negative")
        self._capacity = max(initial_capacity, 16)
        self._size = 0
        self._alive_count = 0
        self._next_id = 1
        self._free: list[int] = []
        self._traits: dict[int, tuple[Genome, int, int | None, int]] = {}
        self._index_by_id: dict[int, int] = {}
        self._ids: NDArray[np.int64] = np.zeros(self._capacity, dtype=np.int64)
        self._x: NDArray[np.int32] = np.zeros(self._capacity, dtype=np.int32)
        self._y: NDArray[np.int32] = np.zeros(self._capacity, dtype=np.int32)
        self._health: NDArray[np.float64] = np.zeros(self._capacity, dtype=np.float64)
        self._energy: NDArray[np.float64] = np.zeros(self._capacity, dtype=np.float64)
        self._age: NDArray[np.int64] = np.zeros(self._capacity, dtype=np.int64)
        self._vision: NDArray[np.float64] = np.zeros(self._capacity, dtype=np.float64)
        self._alive: NDArray[np.bool_] = np.zeros(self._capacity, dtype=np.bool_)

    def __len__(self) -> int:
        return self._alive_count

    @property
    def next_id(self) -> int:
        return self._next_id

    def create(
        self,
        position: Position,
        *,
        health: float,
        energy: float,
        vision_radius: float,
        genome: Genome | None = None,
        generation: int = 0,
        parent_id: int | None = None,
    ) -> AgentView:
        if not all(isfinite(v) for v in (health, energy, vision_radius)):
            raise ValueError("agent state must be finite")
        if health <= 0.0:
            raise ValueError("initial health must be positive")
        if energy < 0.0:
            raise ValueError("initial energy cannot be negative")
        if vision_radius < 0.0:
            raise ValueError("vision radius cannot be negative")
        if not self._free and self._size == self._capacity:
            self._grow()

        if self._free:
            index = heapq.heappop(self._free)
        else:
            index = self._size
            self._size += 1
        agent_id = self._next_id
        self._traits[agent_id] = (genome or Genome(), generation, parent_id, -1)
        self._alive_count += 1
        self._next_id += 1
        self._index_by_id[agent_id] = index
        self._ids[index] = agent_id
        self._x[index] = position.x
        self._y[index] = position.y
        self._health[index] = health
        self._energy[index] = energy
        self._age[index] = 0
        self._vision[index] = vision_radius
        self._alive[index] = True
        return self._view_at(index)

    def get(self, agent_id: int) -> AgentView:
        return self._view_at(self._require_index(agent_id))

    def living(self) -> tuple[AgentView, ...]:
        return tuple(self.get(agent_id) for agent_id in sorted(self._index_by_id))

    def spend(self, agent_id: int, amount: float) -> None:
        if not isfinite(amount) or amount < 0:
            raise ValueError("energy cost must be finite and nonnegative")
        index = self._require_living_index(agent_id)
        if amount > self._energy[index]:
            raise ValueError("agent has insufficient energy")
        self._energy[index] -= amount

    def feed(self, agent_id: int, amount: float, maximum: float) -> float:
        if not isfinite(amount) or amount < 0 or not isfinite(maximum):
            raise ValueError("invalid feeding amount")
        index = self._require_living_index(agent_id)
        gained = min(amount, max(0.0, maximum - self._energy[index]))
        self._energy[index] += gained
        return float(gained)

    def mark_reproduction(self, agent_id: int, tick: int) -> None:
        genome, generation, parent, _ = self._traits[agent_id]
        self._traits[agent_id] = (genome, generation, parent, tick)

    def remove(self, agent_id: int) -> AgentView:
        view = self.get(agent_id)
        index = self._index_by_id.pop(agent_id)
        self._alive[index] = False
        del self._traits[agent_id]
        self._alive_count -= 1
        heapq.heappush(self._free, index)
        return view

    def age_all(self) -> None:
        self._age[: self._size] += self._alive[: self._size].astype(np.int64)

    def move(
        self, agent_id: int, destination: Position, energy_cost: float
    ) -> AgentView:
        if not isfinite(energy_cost) or energy_cost < 0.0:
            raise ValueError("movement energy cost cannot be negative")
        index = self._require_living_index(agent_id)
        if self._energy[index] < energy_cost:
            raise ValueError("agent has insufficient energy")
        self._x[index] = destination.x
        self._y[index] = destination.y
        self._energy[index] -= energy_cost
        return self._view_at(index)

    def state_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(self._size.to_bytes(8, "little"))
        digest.update(self._alive_count.to_bytes(8, "little"))
        digest.update(self._next_id.to_bytes(8, "little"))
        digest.update(
            json.dumps(
                [
                    (
                        a.id,
                        a.genome.values(),
                        a.generation,
                        a.parent_id,
                        a.last_reproduction,
                    )
                    for a in self.living()
                ]
            ).encode()
        )
        digest.update(json.dumps(sorted(self._free)).encode())
        for array in (
            self._ids,
            self._x,
            self._y,
            self._health,
            self._energy,
            self._age,
            self._vision,
            self._alive,
        ):
            digest.update(array[: self._size].tobytes(order="C"))
        return digest.hexdigest()

    def _view_at(self, index: int) -> AgentView:
        genome, generation, parent, last = self._traits[int(self._ids[index])]
        return AgentView(
            id=int(self._ids[index]),
            position=Position(int(self._x[index]), int(self._y[index])),
            health=float(self._health[index]),
            energy=float(self._energy[index]),
            age=int(self._age[index]),
            vision_radius=float(self._vision[index]),
            alive=bool(self._alive[index]),
            genome=genome,
            generation=generation,
            parent_id=parent,
            last_reproduction=last,
        )

    def _require_index(self, agent_id: int) -> int:
        try:
            return self._index_by_id[agent_id]
        except KeyError as error:
            raise KeyError(f"unknown agent ID {agent_id}") from error

    def _require_living_index(self, agent_id: int) -> int:
        index = self._require_index(agent_id)
        if not self._alive[index]:
            raise ValueError(f"agent {agent_id} is not alive")
        return index

    def _grow(self) -> None:
        new_capacity = self._capacity * 2
        self._ids = _expanded(self._ids, new_capacity)
        self._x = _expanded(self._x, new_capacity)
        self._y = _expanded(self._y, new_capacity)
        self._health = _expanded(self._health, new_capacity)
        self._energy = _expanded(self._energy, new_capacity)
        self._age = _expanded(self._age, new_capacity)
        self._vision = _expanded(self._vision, new_capacity)
        self._alive = _expanded(self._alive, new_capacity)
        self._capacity = new_capacity


def _expanded[T: np.generic](array: NDArray[T], capacity: int) -> NDArray[T]:
    result = np.zeros(capacity, dtype=array.dtype)
    result[: len(array)] = array
    return result
