import csv
import json
import subprocess
import sys
from pathlib import Path

from agentic_simulation.config import load_config

ROOT = Path(__file__).parents[1]
EXPERIMENT = ROOT / "experiments" / "food_regeneration"
CONDITIONS = ("scarce", "baseline", "abundant")


def test_food_regeneration_is_the_only_condition_difference() -> None:
    configs = {
        name: load_config(EXPERIMENT / f"{name}.yaml").model_dump(mode="json")
        for name in CONDITIONS
    }
    rates = {
        name: config["world"]["resources"]["food"].pop("regeneration_rate")
        for name, config in configs.items()
    }

    assert rates == {"scarce": 0.03, "baseline": 0.15, "abundant": 0.30}
    assert configs["scarce"] == configs["baseline"] == configs["abundant"]


def test_experiment_runner_is_deterministic(tmp_path: Path) -> None:
    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        command = [
            sys.executable,
            str(EXPERIMENT / "run.py"),
            "--ticks",
            "2",
            "--sample-every",
            "1",
            "--seeds",
            "11",
            "--output",
            str(output),
        ]
        subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    for filename in ("metrics.jsonl", "run_summary.csv", "condition_summary.csv"):
        assert (outputs[0] / filename).read_bytes() == (
            outputs[1] / filename
        ).read_bytes()

    manifest = json.loads((outputs[0] / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert manifest["seeds"] == [11]
    with (outputs[0] / "run_summary.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert [row["condition"] for row in rows] == list(CONDITIONS)
    assert {row["tick"] for row in rows} == {"2"}

    original = (outputs[0] / "metrics.jsonl").read_bytes()
    repeated = subprocess.run(
        [*command[:-1], str(outputs[0])],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert repeated.returncode != 0
    assert (outputs[0] / "metrics.jsonl").read_bytes() == original
