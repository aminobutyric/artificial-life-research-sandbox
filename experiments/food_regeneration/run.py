"""Run the predefined food-regeneration experiment matrix."""

from __future__ import annotations

import argparse
import csv
import json
import platform
import subprocess
import sys
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

from agentic_simulation import __version__
from agentic_simulation.config import AppConfig, load_config
from agentic_simulation.simulation import SimulationBuilder

EXPERIMENT_ID = "food-regeneration-001"
DEFAULT_SEEDS = (11, 23, 37, 53, 71)
DEFAULT_TICKS = 1_000
DEFAULT_SAMPLE_EVERY = 50
EXPERIMENT_DIRECTORY = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXPERIMENT_DIRECTORY.parents[1]
CONDITIONS = (
    ("scarce", EXPERIMENT_DIRECTORY / "scarce.yaml"),
    ("baseline", EXPERIMENT_DIRECTORY / "baseline.yaml"),
    ("abundant", EXPERIMENT_DIRECTORY / "abundant.yaml"),
)
GENES = (
    "exploration",
    "food_attraction",
    "reproduction_threshold",
    "metabolism",
    "vision",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ticks", type=int, default=DEFAULT_TICKS)
    parser.add_argument("--sample-every", type=int, default=DEFAULT_SAMPLE_EVERY)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    return parser


def _resolved_config(path: Path, seed: int) -> AppConfig:
    config = load_config(path)
    world = config.world.model_copy(update={"seed": seed})
    return config.model_copy(update={"world": world})


def _git_metadata() -> dict[str, Any]:
    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=REPOSITORY_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    revision = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {
        "commit": revision.stdout.strip() if revision.returncode == 0 else None,
        "dirty": status.returncode != 0 or bool(status.stdout.strip()),
    }


def _sample(simulation: Any, condition: str, seed: int) -> dict[str, Any]:
    update = simulation.visualization_update()
    agents = update["agent_statistics"]
    resources = update["statistics"]["resource_totals"]
    return {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "condition": condition,
        "seed": seed,
        "tick": simulation.tick,
        "population": agents["population"],
        "births": agents["births"],
        "deaths": agents["deaths"],
        "average_energy": agents["average_energy"],
        "average_age": agents["average_age"],
        "max_generation": agents["max_generation"],
        "genetic_diversity": agents["genetic_diversity"],
        "mean_genome": agents["mean_genome"],
        "food_total": resources["food"],
        "water_total": resources["water"],
    }


def _time_averaged_population(samples: list[dict[str, Any]], ticks: int) -> float:
    if ticks == 0:
        return float(samples[0]["population"])
    area = sum(
        (left["population"] + right["population"]) * (right["tick"] - left["tick"]) / 2
        for left, right in pairwise(samples)
    )
    return round(area / ticks, 4)


def _run_one(
    condition: str,
    path: Path,
    seed: int,
    ticks: int,
    sample_every: int,
    metrics: Any,
) -> dict[str, Any]:
    simulation = SimulationBuilder.build(_resolved_config(path, seed))
    samples = [_sample(simulation, condition, seed)]
    metrics.write(json.dumps(samples[-1], sort_keys=True) + "\n")
    extinction_tick: int | None = None

    for _ in range(ticks):
        simulation.advance()
        if extinction_tick is None and len(simulation.agents) == 0:
            extinction_tick = simulation.tick
        if simulation.tick % sample_every == 0 or simulation.tick == ticks:
            samples.append(_sample(simulation, condition, seed))
            metrics.write(json.dumps(samples[-1], sort_keys=True) + "\n")

    final = samples[-1]
    summary: dict[str, Any] = {
        "experiment_id": EXPERIMENT_ID,
        "condition": condition,
        "seed": seed,
        "tick": ticks,
        "state_hash": simulation.state_hash(),
        "extinct": final["population"] == 0,
        "extinction_tick": extinction_tick,
        "final_population": final["population"],
        "peak_sampled_population": max(sample["population"] for sample in samples),
        "time_averaged_population": _time_averaged_population(samples, ticks),
        "births": final["births"],
        "deaths": final["deaths"],
        "average_energy": final["average_energy"],
        "average_age": final["average_age"],
        "max_generation": final["max_generation"],
        "genetic_diversity": final["genetic_diversity"],
        "food_total": final["food_total"],
    }
    summary.update({f"mean_{gene}": final["mean_genome"][gene] for gene in GENES})
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _condition_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for condition, _ in CONDITIONS:
        selected = [row for row in rows if row["condition"] == condition]
        populations = [row["final_population"] for row in selected]
        peaks = [row["peak_sampled_population"] for row in selected]
        averaged = [row["time_averaged_population"] for row in selected]
        summary: dict[str, Any] = {
            "experiment_id": EXPERIMENT_ID,
            "condition": condition,
            "runs": len(selected),
            "extinct_runs": sum(bool(row["extinct"]) for row in selected),
            "median_final_population": median(populations),
            "min_final_population": min(populations),
            "max_final_population": max(populations),
            "median_peak_sampled_population": median(peaks),
            "max_peak_sampled_population": max(peaks),
            "median_time_averaged_population": median(averaged),
            "median_births": median(row["births"] for row in selected),
            "median_deaths": median(row["deaths"] for row in selected),
            "median_average_energy": median(row["average_energy"] for row in selected),
            "median_genetic_diversity": median(
                row["genetic_diversity"] for row in selected
            ),
            "median_food_total": median(row["food_total"] for row in selected),
        }
        summary.update(
            {
                f"median_mean_{gene}": median(row[f"mean_{gene}"] for row in selected)
                for gene in GENES
            }
        )
        summaries.append(summary)
    return summaries


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.ticks < 0:
        parser.error("--ticks cannot be negative")
    if args.sample_every < 1:
        parser.error("--sample-every must be positive")
    if len(set(args.seeds)) != len(args.seeds) or any(seed < 0 for seed in args.seeds):
        parser.error("--seeds must be unique nonnegative integers")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    args.output.mkdir(parents=True)

    configs = {
        condition: load_config(path).model_dump(mode="json")
        for condition, path in CONDITIONS
    }
    manifest = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "status": "running",
        "ticks": args.ticks,
        "sample_every": args.sample_every,
        "seeds": args.seeds,
        "conditions": [condition for condition, _ in CONDITIONS],
        "configs": configs,
        "software": {
            "agentic_simulation": __version__,
            "python": platform.python_version(),
            "git": _git_metadata(),
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summaries: list[dict[str, Any]] = []
    with (args.output / "metrics.jsonl").open("x", encoding="utf-8") as metrics:
        for condition, path in CONDITIONS:
            for seed in args.seeds:
                summary = _run_one(
                    condition,
                    path,
                    seed,
                    args.ticks,
                    args.sample_every,
                    metrics,
                )
                summaries.append(summary)
                print(
                    f"completed {condition} seed {seed}: "
                    f"population {summary['final_population']}",
                    file=sys.stderr,
                    flush=True,
                )

    _write_csv(args.output / "run_summary.csv", summaries)
    _write_csv(args.output / "condition_summary.csv", _condition_summaries(summaries))
    manifest["status"] = "complete"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "experiment_id": EXPERIMENT_ID,
                "runs": len(summaries),
                "output": str(args.output),
                "manifest": str(manifest_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
