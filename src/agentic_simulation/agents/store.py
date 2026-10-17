"""Array-oriented agent state with stable identities."""

from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import asdict
from math import isfinite
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from agentic_simulation.config import StrictModel
from agentic_simulation.world import Position

from .genome import Genome
from .models import AgentView


class SavedTrait(StrictModel):
    id: int = Field(ge=1, strict=True)
    genome: Genome
    generation: int = Field(ge=0, strict=True)
    parent_id: int | None = Field(default=None, ge=1, strict=True)
    last_reproduction: int = Field(ge=-1, strict=True)


class SavedAgentState(StrictModel):
    next_id: int = Field(ge=1, le=2**63 - 1, strict=True)
    traits: tuple[SavedTrait, ...]


AGENT_ARRAY_DTYPES = {
    "ids": np.dtype("int64"),
    "x": np.dtype("int32"),
    "y": np.dtype("int32"),
    "health": np.dtype("float64"),
    "energy": np.dtype("float64"),
    "age": np.dtype("int64"),
    "vision": np.dtype("float64"),
    "alive": np.dtype("bool"),
}


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

    def checkpoint_state(self) -> tuple[dict[str, Any], dict[str, NDArray[Any]]]:
        """Copy all used slots, including dead-slot bytes needed by the state hash."""
        metadata = {
            "next_id": self.next_id,
            "traits": [
                {
                    "id": a.id,
                    "genome": asdict(a.genome),
                    "generation": a.generation,
                    "parent_id": a.parent_id,
                    "last_reproduction": a.last_reproduction,
                }
                for a in self.living()
            ],
        }
        arrays = {
            name: getattr(self, f"_{name}")[: self._size].copy()
            for name in AGENT_ARRAY_DTYPES
        }
        return metadata, arrays

    @classmethod
    def from_checkpoint(
        cls,
        metadata: dict[str, Any],
        arrays: dict[str, NDArray[Any]],
    ) -> AgentStore:
        state = SavedAgentState.model_validate(metadata)
        if set(arrays) != set(AGENT_ARRAY_DTYPES):
            raise ValueError("checkpoint agent arrays are missing or unexpected")
        ids = arrays["ids"]
        if ids.ndim != 1:
            raise ValueError("checkpoint agent arrays must be one-dimensional")
        size = len(ids)
        for name, dtype in AGENT_ARRAY_DTYPES.items():
            array = arrays[name]
            if array.shape != (size,) or array.dtype != dtype:
                raise ValueError(f"invalid checkpoint agent array shape/dtype: {name}")
            if not np.isfinite(array).all():
                raise ValueError(f"checkpoint agent array must be finite: {name}")
        if size and (np.min(ids) < 1 or state.next_id <= int(np.max(ids))):
            raise ValueError("checkpoint agent ID allocator would reuse an ID")
        if len(np.unique(ids)) != size:
            raise ValueError("duplicate agent IDs in checkpoint")
        if any(
            np.any(arrays[name] < 0) for name in ("age", "health", "energy", "vision")
        ):
            raise ValueError("negative checkpoint agent state")
        alive_ids = set(map(int, ids[arrays["alive"]]))
        if (
            len(state.traits) != len(alive_ids)
            or {a.id for a in state.traits} != alive_ids
        ):
            raise ValueError("checkpoint traits do not match living agents")
        for trait in state.traits:
            if trait.parent_id is not None and trait.parent_id >= trait.id:
                raise ValueError("checkpoint parent ID must precede child ID")
        store = cls(initial_capacity=size)
        store._size = size
        store._next_id = state.next_id
        store._alive_count = len(alive_ids)
        for name in AGENT_ARRAY_DTYPES:
            getattr(store, f"_{name}")[:size] = arrays[name]
        store._index_by_id = {int(ids[i]): i for i in range(size) if arrays["alive"][i]}
        store._free = [int(i) for i in np.flatnonzero(~arrays["alive"])]
        heapq.heapify(store._free)
        store._traits = {
            a.id: (a.genome, a.generation, a.parent_id, a.last_reproduction)
            for a in state.traits
        }
        return store

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
