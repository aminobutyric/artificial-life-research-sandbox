import json
from pathlib import Path

import pytest

from agentic_simulation.cli import main


def test_headless_lifecycle_log_is_complete_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    log = tmp_path / "events.jsonl"
    command = [
        "--config",
        "experiments/starvation.yaml",
        "run",
        "--ticks",
        "300",
        "--events",
        str(log),
    ]
    assert main(command) == 0
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert records[0]["kind"] == "initial"
    assert records[-1]["kind"] == "complete"
    assert records[-1]["tick"] == 300
    deaths = [
        event
        for row in records
        if row["kind"] == "tick"
        for event in row["life_events"]
        if event["event_type"] == "agent_died"
    ]
    assert len(deaths) == 30
    assert {e["reason"] for e in deaths} == {"starvation"}
    original = log.read_bytes()
    with pytest.raises(FileExistsError):
        main(command)
    assert log.read_bytes() == original


def test_missing_config_does_not_silently_run_another_experiment(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        main(["--config", str(tmp_path / "absent.yaml"), "run", "--ticks", "1"])
