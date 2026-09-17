# Agentic Simulation

A deterministic, headless-first artificial-life simulation. V1 establishes the
world and environment before introducing agents.

Agents support genetic foraging, metabolism, food consumption, death, asexual
reproduction, and mutation. The current phase improves experiment control and
live agent inspection. See the [survival rules](docs/survival-evolution.md) and
[observatory controls](docs/observatory.md).

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
- live visualization with play, pause, step, speed control, and bounded runs;
- click-to-follow agent inspection, shared run status, and visible errors;
- array-backed agents with stable identities and spatial indexing;
- immutable observations, pluggable brains, and centrally resolved actions;
- genetic foragers and a deterministic random-movement baseline.

The finalized semantics are recorded in the
[V1 world and environment contract](docs/world-v1.md).
The first agent boundary is documented in
[Agent Phase 1 contract](docs/agents-v1.md).

## Run locally

Python 3.12 and [`uv`](https://docs.astral.sh/uv/) are required.

```console
uv sync
uv run alife --config experiments/world.yaml serve
```

Open <http://127.0.0.1:8000>. The world starts paused so it can be inspected at
tick zero. Keep this terminal open while using the browser.

- **Play** advances continuously; **Pause** stops after the current tick.
- **Run & pause** advances by the entered number of additional ticks, then stops.
  Enter 1,000 here to watch a 1,000-tick experiment in the browser.
- **Set speed** changes the target ticks per second. Actual speed depends on CPU
  and population; the status line displays tick cost and CPU limits.
- Click an agent to follow it as it moves. Click a shared cell again to cycle
  through its agents. The inspector retains its last observed state after death.
- **Regenerate & pause** replaces the run at tick zero and clears the selection.

To run without visualization and save a resumable **environment-only** snapshot
(this does not checkpoint agents):

```console
uv run alife --config experiments/world.yaml generate \
  --ticks 1000 \
  --output world-1000.npz
```

To run the agent simulation headlessly, without starting a browser server:

```console
uv run alife --config experiments/world.yaml run --ticks 1000
```

The headless command prints a JSON summary when finished. Progress goes to
stderr every 50 ticks, so stdout stays valid JSON. Use
`--progress-every 0` for quiet output or choose another reporting interval.

Append `--events life.jsonl` to record lifecycle events and actions. The default
experiment uses genetic brains; `experiments/starvation.yaml` demonstrates
extinction without food. Restart the running server after updating code, then
refresh the browser to load the survival UI.

The same configuration and seed produce the same initial world and update
sequence. A saved snapshot contains all environmental layers, resource state,
the current tick, configuration, ID allocator, and runtime RNG state.

## Architecture boundary

```text
agentic_simulation.world          deterministic domain model
            ↑
agentic_simulation.visualization  read-only snapshots and controls
```

The world package does not import FastAPI or any visualization code. Agents
consume immutable observations and submit intents; only the world
and its resolution systems will change physical state.

## Tests

```console
uv run pytest
uv run ruff check .
uv run mypy src
```
