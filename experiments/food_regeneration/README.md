# Experiment 001: food regeneration and population dynamics

## Question

How does food regeneration change population persistence, reproduction, energy,
and survivor genome composition in the current genetic-forager model?

This is the first defined experiment and also validates the experiment pipeline.
It studies this software model; it does not make a biological claim.

## Design

The only condition-level change is food regeneration per node per tick:

| Condition | Regeneration |
| --- | ---: |
| Scarce | 0.03 |
| Baseline | 0.15 |
| Abundant | 0.30 |

All conditions use a 50 × 50 world, 60 initially populated food nodes, 100
founders, a population cap of 1,000, genetic brains, and the same lifecycle rules.
Water nodes are set to zero because thirst and drinking are not implemented.

- Seeds: 11, 23, 37, 53, 71.
- Horizon: 1,000 ticks.
- Sampling: tick 0 and every 50 ticks, including the final tick.
- Replicates: 5 per condition; 15 runs total.
- No run is excluded because it becomes extinct. A technical failure leaves an
  incomplete manifest and must be investigated, then the complete matrix rerun.

## Predefined expectations

1. Abundant food will have a higher median time-averaged population than scarce
   food.
2. Abundant food will have a higher median cumulative birth count at tick 1,000.
3. Extinction count, final energy, generation, genetic diversity, and the five
   survivor mean genes are secondary descriptive outcomes. Their direction is
   not predefined.

These comparisons are descriptive at five seeds. Do not add or remove seeds
after viewing results under this experiment identifier.

## Run

From the repository root:

```console
uv sync
uv run pytest
uv run python experiments/food_regeneration/run.py \
  --output runs/food-regeneration-001
```

The output directory must not already exist. The runner records resolved configs
and source metadata, samples metrics without retaining agent histories, and
prints only the artifact paths to stdout. Progress is written to stderr.

To validate the pipeline without producing Experiment 001 results:

```console
uv run python experiments/food_regeneration/run.py \
  --ticks 50 --sample-every 10 --seeds 11 \
  --output runs/food-regeneration-smoke
```

Changing ticks, seeds, sampling, configs, or code creates a pilot or a new
experiment version; it is not the predefined Experiment 001 result.

## Outcomes

Primary run-level outcomes:

- final population;
- peak sampled population, used to identify population-cap censoring;
- time-averaged population, calculated by trapezoidal integration of sampled
  population divided by the tick horizon;
- cumulative births at tick 1,000.

Secondary outcomes include deaths, extinction tick, mean energy, mean age,
highest living generation, genetic diversity, mean genome traits, and final food
availability. Condition summaries report medians and ranges; raw sampled rows
remain in `metrics.jsonl`.

## Boundaries

Food placement and founder genomes both change with seed, intentionally forming
one replicate of the whole seeded world. The experiment does not isolate
mutation, estimate causal effects of individual genes, or distinguish old-age
from starvation deaths in its interval summary. Ascending agent-ID resolution
can favor earlier agents. Results cannot yet resume from checkpoints.
