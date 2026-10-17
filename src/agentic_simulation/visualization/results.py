"""Durable execution records, independent of simulation state and checkpoints."""

from __future__ import annotations

import csv
import io
import json
import platform
import subprocess
import time
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def source_version() -> dict[str, Any]:
    metadata: dict[str, Any] = {"python": platform.python_version()}
    try:
        root = Path(__file__).resolve().parents[3]
        metadata["commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            timeout=2,
            stderr=subprocess.DEVNULL,
        ).strip()
        metadata["dirty"] = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=root,
                text=True,
                timeout=2,
                stderr=subprocess.DEVNULL,
            ).strip()
        )
    except (OSError, subprocess.SubprocessError):
        metadata["git_unavailable"] = True
    return metadata


def population_svg(metrics: list[dict[str, Any]]) -> str:
    if not metrics:
        return "<p>No successful samples recorded.</p>"
    first, last = metrics[0]["tick"], metrics[-1]["tick"]
    maximum = max(1, max(row["population"] for row in metrics))
    points = " ".join(
        f"{50 + 630 * (row['tick'] - first) / max(1, last - first):.2f},"
        f"{220 - 190 * row['population'] / maximum:.2f}"
        for row in metrics
    )
    return (
        '<svg viewBox="0 0 720 270" role="img" '
        'aria-label="Sampled living population by simulation tick">'
        "<title>Sampled population trajectory</title>"
        '<path d="M50 30V220H680" fill="none" stroke="currentColor"/>'
        f'<polyline points="{points}" fill="none" stroke="#5bb9d4" '
        'stroke-width="3"/>'
        f'<circle cx="50" cy="{220 - 190 * metrics[0]["population"] / maximum:.2f}" '
        'r="3" fill="#5bb9d4"/>'
        f'<text x="8" y="35" fill="currentColor">{maximum}</text>'
        '<text x="25" y="225" fill="currentColor">0</text>'
        f'<text x="50" y="244" fill="currentColor">{first}</text>'
        f'<text x="680" y="244" text-anchor="end" fill="currentColor">{last}</text>'
        '<text x="360" y="265" text-anchor="middle" fill="currentColor">Tick</text>'
        "</svg>"
    )


def report_html(result: dict[str, Any]) -> str:
    summary = result["summary"]

    def display(value: Any) -> str:
        if value is None:
            return "Unavailable"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return escape(str(value))

    rows = "".join(
        f"<tr><th>{escape(key.replace('_', ' ').title())}</th>"
        f"<td>{display(summary[key])}</td></tr>"
        for key in (
            "status",
            "reason",
            "core",
            "seed",
            "start_tick",
            "end_tick",
            "elapsed_ticks",
            "initial_population",
            "final_population",
            "births",
            "deaths",
            "extinct",
            "extinction_tick",
            "peak_population",
            "elapsed_seconds",
        )
    )
    configuration = escape(json.dumps(result["manifest"]["configuration"], indent=2))
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Experiment result — {escape(summary["status"])}</title>
<style>body{{font:16px/1.6 system-ui;background:#101614;color:#f2efe5;
max-width:900px;margin:40px auto;padding:0 20px}}h1{{line-height:1.2}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:8px;border-bottom:1px solid #34433c;
text-align:left}}td{{text-align:right}}svg{{width:100%;height:auto}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}summary{{cursor:pointer}}
@media print{{body{{background:white;color:black}}}}</style>
<h1>Experiment result: {escape(summary["status"])}</h1>
<p>Execution {escape(summary["result_id"])}. Changes are measured from the start
of this execution segment; its configuration remains fixed.</p>
<table>{rows}</table><h2>Population trajectory</h2>
{population_svg(result["metrics"])}
<p>Samples: segment start, every {result["manifest"]["sample_every"]} ticks,
and its final successful tick. Peaks and extinction are checked every tick.
An interrupted or failed execution is not a completed experiment.</p>
<details><summary>Resolved configuration</summary><pre>{configuration}</pre></details>
<details><summary>Manifest and source version</summary><pre>
{escape(json.dumps(result["manifest"], indent=2))}</pre></details></html>"""


class ResultStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, result_id: str) -> Path:
        if str(UUID(result_id)) != result_id:
            raise ValueError("Invalid result ID")
        path = self.directory / result_id
        if path.is_symlink():
            raise ValueError("Invalid result directory")
        return path

    def read(self, result_id: str) -> dict[str, Any]:
        path = self.path(result_id)
        manifest = json.loads((path / "manifest.json").read_text())
        metrics_path = path / "metrics.jsonl"
        metrics = []
        lines = metrics_path.read_text().splitlines() if metrics_path.exists() else []
        for index, line in enumerate(lines):
            try:
                metrics.append(json.loads(line))
            except ValueError:
                # A killed process may leave its final append partially written.
                if manifest["status"] != "running" or index != len(lines) - 1:
                    raise
        summary_path = path / "summary.json"
        last = metrics[-1] if metrics else {}
        summary = (
            json.loads(summary_path.read_text())
            if summary_path.exists()
            else {
                **manifest["initial"],
                "status": "incomplete",
                "reason": "Completion unrecorded; showing saved samples only.",
                "end_tick": last.get("tick", manifest["start_tick"]),
                "elapsed_ticks": last.get("tick", manifest["start_tick"])
                - manifest["start_tick"],
                "elapsed_seconds": None,
                "final_population": last.get("population"),
                "births": last.get("births"),
                "deaths": last.get("deaths"),
                "extinct": last.get("population") == 0 if metrics else None,
                "extinction_tick": None,
                "peak_population": None,
            }
        )
        if manifest["status"] == "running":
            summary = {
                **summary,
                "status": "incomplete",
                "reason": "Completion unrecorded; showing saved samples only.",
            }
        return {"manifest": manifest, "summary": summary, "metrics": metrics}

    def list_results(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        results = []
        for path in self.directory.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                # Listing needs no time-series parse, even for long experiments.
                manifest = json.loads(
                    (self.path(path.name) / "manifest.json").read_text()
                )
                summary_path = path / "summary.json"
                summary = (
                    json.loads(summary_path.read_text())
                    if summary_path.exists()
                    else manifest["initial"]
                )
                if manifest["status"] == "running":
                    summary = {**summary, "status": "incomplete"}
                results.append(summary)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(results, key=lambda row: row["started_at"], reverse=True)

    def archive(self, result_id: str) -> bytes:
        result = self.read(result_id)
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name, value in (
                ("manifest.json", json.dumps(result["manifest"], indent=2)),
                ("summary.json", json.dumps(result["summary"], indent=2)),
                (
                    "metrics.jsonl",
                    "".join(json.dumps(row) + "\n" for row in result["metrics"]),
                ),
                ("report.html", report_html(result)),
            ):
                archive.writestr(name, value)
            csv_output = io.StringIO()
            writer = csv.DictWriter(csv_output, fieldnames=list(result["summary"]))
            writer.writeheader()
            writer.writerow(result["summary"])
            archive.writestr("summary.csv", csv_output.getvalue())
        return output.getvalue()


class ExecutionRecord:
    sample_every = 50

    def __init__(
        self,
        store: ResultStore | None,
        run_id: str,
        configuration: dict[str, Any],
        frame: dict[str, Any],
        target_tick: int | None,
        initial_hash: str,
    ) -> None:
        self.result_id = str(uuid4())
        self.store = store
        self.started = time.monotonic()
        self.start_tick: int = frame["tick"]
        self.start_births: int = frame["agent_statistics"]["births"]
        self.start_deaths: int = frame["agent_statistics"]["deaths"]
        self.initial_population: int = frame["agent_statistics"]["population"]
        self.peak_population = self.initial_population
        self.extinction_tick: int | None = (
            self.start_tick if not self.initial_population else None
        )
        self.metrics: list[dict[str, Any]] = []
        self.initial: dict[str, Any] = {
            "result_id": self.result_id,
            "run_id": run_id,
            "status": "running",
            "started_at": timestamp(),
            "start_tick": self.start_tick,
            "core": configuration["agents"]["brain"],
            "seed": configuration["world"]["seed"],
            "target_tick": target_tick,
            "initial_population": self.initial_population,
        }
        self.manifest = {
            "schema_version": 1,
            **self.initial,
            "initial": self.initial,
            "configuration": configuration,
            "sample_every": self.sample_every,
            "software": source_version(),
            "initial_state_hash": initial_hash,
        }
        if store:
            store.path(self.result_id).mkdir(parents=True, exist_ok=False)
            self._write_json("manifest.json", self.manifest)
        self.observe(frame, force=True)

    def _write_json(self, name: str, value: dict[str, Any]) -> None:
        if self.store:
            path = self.store.path(self.result_id) / name
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, indent=2) + "\n")
            temporary.replace(path)

    def observe(self, frame: dict[str, Any], *, force: bool = False) -> None:
        stats = frame["agent_statistics"]
        self.peak_population = max(self.peak_population, stats["population"])
        if not stats["population"] and self.extinction_tick is None:
            self.extinction_tick = frame["tick"]
        if not force and (frame["tick"] - self.start_tick) % self.sample_every:
            return
        if self.metrics and self.metrics[-1]["tick"] == frame["tick"]:
            return
        row = {
            "tick": frame["tick"],
            **stats,
            "births": stats["births"] - self.start_births,
            "deaths": stats["deaths"] - self.start_deaths,
        }
        if self.store:
            with (self.store.path(self.result_id) / "metrics.jsonl").open(
                "a"
            ) as output:
                output.write(json.dumps(row) + "\n")
        self.metrics.append(row)

    def finish(
        self,
        frame: dict[str, Any],
        status: str,
        reason: str,
        state_hash: str | None,
    ) -> dict[str, Any]:
        self.observe(frame, force=True)
        final = self.metrics[-1]
        summary = {
            **self.initial,
            "status": status,
            "reason": reason,
            "finished_at": timestamp(),
            "end_tick": final["tick"],
            "elapsed_ticks": final["tick"] - self.start_tick,
            "elapsed_seconds": round(time.monotonic() - self.started, 3),
            "final_population": final["population"],
            "population_change": final["population"] - self.initial_population,
            "births": final["births"],
            "deaths": final["deaths"],
            "extinct": self.extinction_tick is not None,
            "extinction_tick": self.extinction_tick,
            "peak_population": self.peak_population,
            "average_energy": final["average_energy"],
            "state_hash": state_hash,
        }
        manifest = {
            **self.manifest,
            "status": status,
            "finished_at": summary["finished_at"],
        }
        result = {"summary": summary, "manifest": manifest, "metrics": self.metrics}
        self._write_json("summary.json", summary)
        if self.store:
            path = self.store.path(self.result_id)
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=list(summary))
            writer.writeheader()
            writer.writerow(summary)
            (path / "summary.csv").write_text(output.getvalue())
            (path / "report.html").write_text(report_html(result))
        # Final manifest is the commit marker for a fully saved execution.
        self._write_json("manifest.json", manifest)
        return result
