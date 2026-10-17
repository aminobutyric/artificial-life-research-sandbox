# Run setup and management

The observatory includes a run setup panel. It starts with the configuration
loaded by `alife --config <path> serve` and displays the active settings alongside
the form. Editing the form does not change the simulation.

## Starting an experiment

1. Pause the current run if it is running.
2. Choose a decision core, seed, world size, founder population, resource and
   terrain settings, lifecycle parameters, and tick horizon.
3. Click **Start new experiment**. The server validates the configuration and
   builds a new simulation before replacing the previous one.
4. The new world starts at tick zero and advances to the requested horizon,
   then pauses. The selected agent is cleared when the run changes.

The setup fields are disabled during execution. Pause enables preparation of a
new experiment; changing settings still requires starting a replacement run.
**Reset to active settings** discards form edits. The active core, seed, founder
population, and initial horizon remain visible, with the complete resolved
configuration available under **View all resolved settings**.

Existing execution controls retain their semantics. Manual pause cancels the
current bounded target; Play resumes continuously, while Run & pause uses an
additional tick count. The initial experiment horizon remains metadata even
when these controls change the execution target. Speed changes affect pacing,
not the frozen model configuration. Regenerate & pause creates a new run with
the active model settings and a replacement seed.

Starting or regenerating a run replaces its current state. Execution results
are retained in [saved results](completion-results.md), and reaching a bounded
target opens the completion summary dialog.

## Core catalog

| UI label | Configuration ID | Behavior |
| --- | --- | --- |
| Random movement | `random` | Random adjacent movement or idle; does not eat or reproduce. |
| Reactive forager | `genetic` | Locally seeks food, eats, and reproduces using genome traits; has no memory. |

One core is selected for the entire population. Offspring inherit their parent's
core. Founder genome variation and offspring mutation are distinct from core
selection: founders retain seeded variation, and mutation probability zero
disables offspring mutation. The memory core will become available in item 4.

Idle probability applies only to the random movement core and is disabled in
the form when the reactive forager is selected. Its previous value is preserved
in the resolved configuration. Other fields configure the shared world and
lifecycle. Water nodes are visible resources but do not implement thirst.

Fields not exposed in the form retain their active configuration values.
Resource node counts remain subject to the world's existing placement rules:
actual nodes may be fewer when insufficient suitable cells exist.

## Server boundary

Full world frames from `GET /api/world` and `/ws/world` include:

- `run_configuration`: a copy of the frozen `AppConfig`;
- `core_catalog`: supported IDs, labels, and descriptions;
- `control.core` and `control.horizon`: active core and initial tick horizon.

`POST /api/control/new-experiment` accepts the current `expected_run_id`,
`world`, `agents`, and `life` configuration objects, and an integer `ticks`
between 1 and 1,000,000. The configuration objects follow the schemas in
`config.py`. Visualization settings come from the active server and cannot
be changed through this endpoint. Successful creation returns a complete world
frame at tick zero with a new run ID and publishes it to all connected viewers.

Validation or world-generation failures return HTTP 422 and preserve the active
run. A running simulation or stale `expected_run_id` returns HTTP 409. Unknown
cores and request fields are rejected. Configuration changes are allowed only
through a fresh run; no endpoint edits the active model in place.

## Verification

Automated tests cover validation without replacement, deterministic bounded
execution, stale/running-run rejection, configuration preservation after speed
and step controls, and offspring inheritance for both supported cores. Browser
checks cover both selections, start, validation feedback, reset, configuration
locking, pause, step, inspection, layer switching, and responsive layout.
