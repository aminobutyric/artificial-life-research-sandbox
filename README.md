# Agentic Simulation

A deterministic, headless-first artificial-life simulation. V1 establishes the
world and environment before introducing agents.

The world is a bounded two-dimensional grid containing dense environmental
layers and sparse resource nodes. A FastAPI service and browser Canvas client
visualize sampled world snapshots without participating in simulation logic.

## V1 features

- seeded procedural terrain and environment generation;
- terrain, elevation, temperature, moisture, and fertility layers;
- sparse food and water resource nodes;
- deterministic environmental updates and resource regeneration;
- immutable cell and region observations;
- versioned, resumable NumPy snapshots;
- headless generation and update commands;
- live visualization with play, pause, step, regenerate, and cell inspection.

The finalized semantics are recorded in the
[V1 world and environment contract](docs/world-v1.md).

## Run locally

Python 3.12 and [`uv`](https://docs.astral.sh/uv/) are required.

```console
uv sync
uv run alife --config experiments/world.yaml serve
```

Open <http://127.0.0.1:8000>. The world starts paused so it can be inspected at
tick zero. To run without visualization and save a resumable snapshot:

```console
uv run alife --config experiments/world.yaml generate \
  --ticks 1000 \
  --output world-1000.npz
```

The same configuration and seed produce the same initial world and update
sequence. A saved snapshot contains all environmental layers, resource state,
the current tick, configuration, ID allocator, and runtime RNG state.

## Architecture boundary

```text
agentic_simulation.world          deterministic domain model
            ↑
agentic_simulation.visualization  read-only snapshots and controls
```

The world package does not import FastAPI or any visualization code. Future
agents will consume immutable observations and submit intents; only the world
and its resolution systems will change physical state.

## Tests

```console
uv run pytest
uv run ruff check .
uv run mypy src
```
