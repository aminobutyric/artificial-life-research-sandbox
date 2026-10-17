# Full simulation checkpoints

Full `.alife` checkpoints save the simulation at a completed tick boundary.
Restoring preserves its future evolution under matching model and dependency
versions. Environment-only `.npz` snapshots and result archives are separate
formats and cannot restore an agent simulation.

## Browser workflow

1. Pause the run or wait for its bounded execution to finish.
2. In **Simulation checkpoints**, select **Download checkpoint** and keep the
   `.alife` file.
3. Later, pause the current world, choose the file, and select **Restore
   checkpoint**.
4. The restored world appears at its saved tick, paused. Use **Step**, **Play**,
   or **Run & pause** to continue. The tick count in Run & pause is additional.

Saving is disabled for a running simulation or a run stopped by a failed tick.
Restoring requires a paused world and can recover from a failed run. Invalid,
corrupt, or incompatible files leave the current world unchanged. Requests from
a viewer with a stale run ID are rejected. Restoring gives the visualization a
fresh run ID and clears agent selection; saved execution results remain in
history.

## Command-line workflow

Save a new simulation after 500 ticks:

```console
uv run alife --config experiments/world.yaml run --ticks 500 \
  --checkpoint-out state-500.alife
```

Continue for another 500 ticks and save the new state:

```console
uv run alife run --from-checkpoint state-500.alife --ticks 500 \
  --checkpoint-out state-1000.alife
```

Open the saved state in the observatory:

```console
uv run alife serve --from-checkpoint state-500.alife
```

The saved configuration is authoritative: combining `--config` with
`--from-checkpoint` is rejected. The working directory does not need a YAML
configuration when resuming. Server `--host`, `--port`, and `--results-dir`
options remain available. A checkpoint output must end in `.alife`, its parent
directory must exist, and an existing destination is never overwritten.
Checkpoint files are ignored by Git.

## Python interface

```python
from agentic_simulation.simulation import load_checkpoint, save_checkpoint

save_checkpoint(simulation, "state.alife")
resumed = load_checkpoint("state.alife")
report = resumed.advance()
```

For HTTP or other byte transports, use `checkpoint_bytes(simulation)` and
`restore_checkpoint(data)`. Call the Python API only between successful ticks,
with exclusive access to the simulation; the browser controller enforces this
boundary with its lock and paused-state checks.

## Preserved state

- Full resolved `AppConfig`, current tick, environmental layers, and resources.
- Resource ID allocator and the world runtime RNG state.
- Agent slot arrays, including dead-slot bytes, and the next agent ID.
- Living genomes, positions, energy, health, ages, vision, parent IDs,
  generations, and reproduction cooldown timestamps.
- Lifecycle birth and death counters.
- Every living agent's core identity, core-state version, and JSON payload.
- Decision-system seed, which derives each per-tick/per-agent decision RNG.

Agent ID lookup, free-slot heap, spatial index, perception, and action-resolution
systems are rebuilt from the saved state. Spare allocation capacity is not
simulation state. Rebuilding these derived structures preserves the existing
state hash, subsequent tick reports, ID allocation, and outcomes.

Random movement and reactive forager cores currently save their policy
parameters and have no learned memory. The codec in `brains/checkpoint.py`
uses an explicit allowlist and versioned JSON state. A future memory core can
add a validated memory payload and decoder without changing the container
format. Unsupported custom cores or state versions are rejected rather than
silently restored with empty state.

## Execution and provenance

A checkpoint preserves simulation state, not an active execution session.
Playback state, an unfinished target tick, current pacing overrides, result
history, and browser preferences are not resumed. The restored simulation
starts paused; its new-run setup horizon defaults to 1,000 ticks. Continuing
with Run & pause chooses a new additional horizon. The server's network binding
does not change during browser restoration.

Continuing in the browser creates a new result segment. Its manifest records
`checkpoint_origin` with the restored tick and state hash. Existing summaries
remain immutable and are not combined into an apparent uninterrupted result.
The predefined food-regeneration batch runner still restarts its complete
matrix after technical failure; this feature does not change that protocol.

## Format and validation

An `.alife` file is a ZIP archive containing:

```text
manifest.json   schema/model versions, configuration, agents' traits,
                brain states, counters, decision seed, checksums and hashes
world.npz       existing world snapshot payload
agents.npz      typed agent slot arrays
```

The initial checkpoint schema and model version are both 1. NumPy's version is
recorded; exact continuation is scoped to matching code and dependencies, not
guaranteed across arbitrary runtime upgrades. Deliberate incompatible model or
brain-state changes must introduce a new version or an explicit migration.

Loading validates format/version, payload checksums, array headers, dtypes,
shapes and finite values, terrain codes, ID allocation, population/counter
reconciliation, living-agent/core identity, positions, and full state integrity.
No pickle, arbitrary module loading, or filesystem extraction is used. Archives
are limited to 64 MiB, with a 512 MiB expanded-data/header limit. Unknown formats
and oversized uploads are rejected with explicit errors.

The existing simulation `state_hash()` format remains unchanged for continuity
with earlier experiments. Checkpoints also store a `continuation_hash` that
includes the actual decision seed and serialized core state. Both are verified
after restore. These integrity checks detect corruption and inconsistencies;
they are not digital signatures authenticating an author.

Checkpoint files are published atomically from a temporary file in the same
directory, using a non-overwriting link. Temporary files are cleaned up on
failure, and existing targets are preserved.

## HTTP boundary

- `GET /api/checkpoint`: download the paused, healthy simulation as `.alife`.
- `POST /api/control/restore-checkpoint?expected_run_id=<current-run-id>`:
  upload raw checkpoint bytes (`application/octet-stream`) and return the
  complete replacement world frame, paused.

Running/stale requests return HTTP 409, invalid/incompatible files return 422,
and uploads above the size limit return 413.

## Verification

Tests compare exact tick reports and hashes across save/restore for both cores,
including births, mutation, deaths, reused slots, lineage, cooldowns, extinction,
non-default decision seeds, and advanced world RNG state. They also cover
no-overwrite behavior, environment-snapshot rejection, invalid metadata and
arrays, object/pickle rejection, CLI save/resume, browser atomic restore,
active-run rejection, and results provenance. Browser checks exercise an actual
download and file upload, rejected corrupt files, matching continuation hashes,
completion summaries after resume, and desktop/mobile layouts.
