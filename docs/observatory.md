# Observatory control and inspection phase

This phase makes existing survival experiments easier to run and inspect. It
does not change agent decisions, energy accounting, or random number streams.

## Execution modes

`alife --config experiments/world.yaml serve` starts the browser UI and waits
paused at tick zero. `run --ticks N` performs a separate headless experiment and
prints a final JSON result. Running one command does not control the other.

The browser offers continuous play, pause, single step, target speed, and
**Run & pause**. A bounded run targets `current_tick + requested_ticks`.
It pauses exactly at that tick, even when broadcasts are sampled. Pause cancels
the target; a subsequent bounded run counts from the new current tick.
Step and bounded-run requests while running return HTTP 409.

Speed is a wall-clock target, not a change to simulation time or physics.
The last tick cost includes advancing the engine and preparing its update, but
excludes network transfer and browser rendering. A CPU-limited indicator means
this cost alone exceeds the requested interval; it is not an achieved-rate
measurement. Controls and cancellation get time between ticks even if the
target rate cannot be met. A single expensive tick still delays controls until
that tick finishes.

Regeneration rebuilds the configured experiment with a new seed, replaces the
run, resets counters and tick to zero, and pauses. It does not save the previous
run. Changing speed does not change the experiment configuration or state hash.

## Stream and controls

Every world/update frame includes a `run_id`, monotonically increasing
`revision` within the server lifetime, and `control` state. Regeneration changes
the run ID even for the same seed, because agent IDs restart in the new run.
These fields are visualization metadata and never enter the simulation hash.

Controls are shared by all viewers. Pause, speed changes, manual steps, and the
last tick of a bounded run are delivered regardless of tick sampling. Slow
viewers receive the latest state; a queued replacement map survives dropped
updates. Reconnecting viewers receive a complete current frame. The browser
ignores older revisions and clears an agent selection when the run ID changes.

`GET /api/status` reports running state, target tick, remaining ticks, speed,
last tick cost, error state, seed, and state hash. In addition to the existing
play, pause, step, and regenerate routes:

- `POST /api/control/run` accepts `{"ticks": 100}` (1–1,000,000).
- `POST /api/control/speed` accepts `{"ticks_per_second": 8}` (positive, at most
  1,000). Speed and count inputs must be finite.

A tick exception stops the clock and surfaces an error to viewers, with details
logged by the server. Since a failed tick may have partially changed state,
continuing or stepping that run is blocked until regeneration.

## Agent inspection

Clicking an occupied cell selects an agent by stable ID; repeated clicks cycle
through the cell's occupants. A white ring follows the living agent. Its
position, health, energy, age, brain, lineage, and genome update from live frames.
The browser retains only the selected agent's last observation after death;
the exact death tick/reason is not inferred from sampled frames.

Full simulation checkpoints, replay, population-history charts, memory, and
agent communication remain future phases.
