# Experiment practice

This project separates model validation, exploratory runs, and defined
experiments. A simulation completing successfully is evidence that the model
executed; it is not by itself evidence for a biological or social conclusion.

## Before a defined run

Record the following before inspecting outcomes:

1. Research question and directional hypotheses.
2. Conditions and the single parameter changed between them.
3. Fixed seed set, tick horizon, and sampling interval.
4. Primary and secondary outcomes.
5. Rules for failed runs and extinctions.
6. Known model limitations that constrain interpretation.

Commit the protocol, configs, runner, and source code together. Run from a clean
commit when results will be compared or shared. The generated manifest records
the commit and whether its worktree was dirty.

## Reproducibility rules

- A condition config is complete and explicit; it must not rely on changing
  application defaults.
- Replicates differ only by their documented seed unless the protocol says
  otherwise.
- Extinction is an outcome and is never removed from a summary.
- A run directory is immutable. Runners refuse to overwrite one.
- Standard output from a runner is machine-readable JSON. Progress goes to
  standard error.
- Raw per-action event logs are optional diagnostic artifacts. They are too large
  to serve as the default metrics format.
- Results are scoped to the committed model and dependency versions. A changed
  implementation starts a new experiment version.

Full agent state can be saved and resumed using [.alife checkpoints](checkpoints.md).
Restoration creates a new execution segment with checkpoint provenance; any use
in a defined study must follow its predefined continuation/failure rules. The
food-regeneration runner still restarts its complete matrix after technical
failure. `generate` snapshots contain the environment only and are not valid
agent checkpoints.

## Artifact layout

Defined experiment runners write:

```text
runs/<run-name>/
├── manifest.json
├── metrics.jsonl
├── run_summary.csv
└── condition_summary.csv
```

`manifest.json` contains the resolved condition configs, fixed seeds, horizon,
sampling interval, code version, commit, and completion status. `metrics.jsonl`
contains one row per sampling point. `run_summary.csv` has one final row per
condition and seed. `condition_summary.csv` aggregates the predefined run-level
outcomes with medians and ranges.

## Interpretation

The first experiments are pipeline and model-behavior studies. With five seeds,
condition summaries are descriptive. Treat medians and ranges as evidence about
this implementation, not as estimates of natural populations. Increase seeds,
predefine an inferential method, and freeze the model before making statistical
claims.

Current sources of experimental bias include ascending-ID action priority,
multiple agents occupying one cell, inactive water and health mechanics, a
fixed genetic policy, and the absence of memory or social interaction.
