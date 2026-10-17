# Completion summaries and saved results

Browser experiments save results under `runs/browser/<result-id>/` by default.
The server prints the resolved location at startup. To use another location:

```console
uv run alife --config experiments/world.yaml serve --results-dir runs/my-browser-results
```

## Completion dialog

Starting a new experiment or using **Run & pause** creates a bounded execution.
When it reaches its requested target, the clock pauses, results are finalized,
and an **Experiment complete** dialog opens. It shows the core, seed, tick
range, elapsed ticks and wall time, starting and final populations, population
change, births, deaths, extinction, peak population, final average energy, the
sampled population trajectory, and resolved configuration.

Close or Escape dismisses the dialog. **Saved experiment results → View
summary** reopens it. **Open full report** opens a standalone printable HTML
report, and **Download results (.zip)** exports all recorded artifacts.
History remains available after regeneration, replacement runs, and server
restarts when the same results directory is used.

The browser remembers completion IDs in session storage, so a dismissed
completion does not reopen after ordinary control updates, WebSocket
reconnection, or a reload in that tab. A new completion can open a new summary.
When session storage is unavailable, suppression lasts until the page reloads.
Each connected viewer receives completion notices independently.

## Execution segments and statuses

Each execution has its own result ID, separate from the world's run ID. New
experiments begin at tick zero. **Run & pause** after earlier activity records
only its requested segment: starting population is measured at that segment's
start, and births/deaths are counter differences during the segment. Several
executions can therefore share a world run ID without overwriting results.

| Status | Meaning |
| --- | --- |
| `running` | Manifest was created; execution is active. |
| `complete` | The bounded target was reached and all result files were finalized. |
| `interrupted` | Manual pause, regeneration, or graceful shutdown ended the execution. |
| `failed` | A simulation tick or result-recording operation failed. |
| `incomplete` | The saved manifest has no final commit marker, such as after a hard process stop or unsuccessful final save. |

Manual pause saves an interrupted execution without triggering a completion
dialog. Play starts a continuous execution, which is saved as interrupted when
paused or shut down. Resuming starts a new segment. Standalone Step actions are
diagnostic operations and do not create executions or completion dialogs.
Extinction is recorded as an outcome; it does not end a bounded execution before
its target. Repeated Play while running does not start duplicate executions.

On tick failure, the result uses the last successful state and has no final
state hash; a partially modified failed tick is not reported as a valid endpoint.
An execution that never started because storage was unavailable leaves the
current world unchanged and returns HTTP 503. Mid-run recording failures stop
the clock and show a storage error; they never issue a successful completion
notice. An unfinished manifest remains explicitly incomplete when read later.

## Saved artifacts and definitions

```text
runs/browser/<result-id>/
├── manifest.json
├── metrics.jsonl
├── summary.json
├── summary.csv
└── report.html
```

The manifest records the world run ID, result ID, start/target tick, timestamp,
resolved configuration, sampling interval, initial state hash, Python version,
and Git commit/dirty status when available. A Git lookup failure is recorded
explicitly. The final manifest status is written after summary/report files;
it marks a finalized result. Filenames are internal fixed names and result IDs
are validated UUIDs.

Metrics include tick, living population, execution-local births and deaths,
average energy/age, living generation, genome means, and diversity. Sampling
occurs at segment start, every 50 ticks relative to its start, and its final
successful tick, with no duplicate endpoint row. Peak population and first
extinction tick are checked on every successful tick, independent of display
broadcast sampling. Wall time includes pacing and recording overhead.

Completed, interrupted, and failed result directories are not reused. API
downloads rebuild a ZIP from the stored evidence. Reports and downloads for
unfinished executions identify the result as incomplete and use only saved
samples; unavailable endpoint measures remain unavailable. A truncated final
metrics append from an unclean stop can be ignored only for an unfinished
record. The API does not promote unfinished records to completed results.

These are observational results, not full simulation checkpoints. Continuing
from a saved result is not supported until backlog item 3. Browser artifacts
are distinct from the existing predefined food-regeneration batch experiment.

## Read-only endpoints

- `GET /api/results`: list saved execution summaries, newest first.
- `GET /api/results/{result_id}`: manifest, summary, and sampled metrics.
- `GET /api/results/{result_id}/report`: standalone HTML report.
- `GET /api/results/{result_id}/download`: ZIP archive.

Control state includes `active_result_id`, `completed_result_id`, `latest_result`,
and `results_error`. A completed result ID stays available through sampled
frames and reconnects. Summary retrieval is independent of the current world.

## Verification

Automated checks cover exact target completion, interval/end sampling, saved
files and ZIP contents, readback from a new server instance, immutable earlier
results after step/regeneration, segment-local counters, interruption, shutdown,
extinction, partial tick failure, unfinished/truncated records, invalid IDs,
and storage failures at start, during recording, and during finalization.
Browser checks cover automatic opening, Escape/Close, once-per-completion
behavior, reconnect/reload, history reopening, ZIP/report retrieval, a second
completion, manual interruption, world replacement, and desktop/mobile layout.
