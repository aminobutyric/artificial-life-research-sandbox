"""Command-line entry point for world generation and visualization."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

import uvicorn

from agentic_simulation.config import load_config
from agentic_simulation.simulation import (
    SimulationBuilder,
    load_checkpoint,
    save_checkpoint,
)
from agentic_simulation.visualization import create_app
from agentic_simulation.world import WorldBuilder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alife")
    parser.add_argument(
        "--config",
        type=Path,
        help="YAML configuration path (default: experiments/world.yaml)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="serve the live world visualization")
    serve.add_argument("--host", help="override the configured bind address")
    serve.add_argument("--port", type=int, help="override the configured port")
    serve.add_argument(
        "--from-checkpoint", type=Path, help="restore a full .alife checkpoint, paused"
    )
    serve.add_argument(
        "--results-dir",
        type=Path,
        default=Path("runs/browser"),
        help="directory for saved browser experiment results",
    )

    generate = commands.add_parser(
        "generate", help="run headlessly and save a snapshot"
    )
    generate.add_argument("--ticks", type=int, default=0)
    generate.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="run agents headlessly")
    run.add_argument("--ticks", type=int, required=True)
    run.add_argument("--events", type=Path, help="write a new buffered JSONL event log")
    run.add_argument(
        "--from-checkpoint", type=Path, help="continue from a full .alife checkpoint"
    )
    run.add_argument(
        "--checkpoint-out",
        type=Path,
        help="save a full .alife checkpoint at the final tick",
    )
    run.add_argument(
        "--progress-every",
        type=int,
        default=50,
        metavar="TICKS",
        help="report progress to stderr every N ticks; use 0 to disable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    source_checkpoint = getattr(args, "from_checkpoint", None)
    if source_checkpoint and args.config:
        parser.error(
            "--config cannot be combined with --from-checkpoint; "
            "the checkpoint contains its configuration"
        )
    restored = load_checkpoint(source_checkpoint) if source_checkpoint else None
    config = (
        restored.config
        if restored
        else load_config(args.config or Path("experiments/world.yaml"))
    )

    if args.command == "serve":
        host = args.host or config.visualization.host
        port = args.port or config.visualization.port
        print(
            f"Browser visualization: http://{host}:{port}\n"
            "Starts paused. Open the address, then click Play or Run & pause.\n"
            f"Saved browser results: {args.results_dir.resolve()}",
            file=sys.stderr,
            flush=True,
        )
        uvicorn.run(
            create_app(
                config, results_directory=args.results_dir, initial_simulation=restored
            ),
            host=host,
            port=port,
        )
        return 0

    if args.ticks < 0:
        raise SystemExit("--ticks cannot be negative")
    if args.command == "run":
        if args.progress_every < 0:
            raise SystemExit("--progress-every cannot be negative")
        browser_command = (
            f"alife serve --from-checkpoint {source_checkpoint}"
            if source_checkpoint
            else f"alife --config {args.config or 'experiments/world.yaml'} serve"
        )
        if args.progress_every:
            print(
                f"Starting headless simulation for {args.ticks} ticks.\n"
                f"For the browser visualization, use '{browser_command}'.",
                file=sys.stderr,
                flush=True,
            )
        simulation = restored or SimulationBuilder.build(config)
        start_tick = simulation.tick
        with (
            args.events.open("x", encoding="utf-8") if args.events else nullcontext()
        ) as log:
            if log:
                log.write(
                    json.dumps(
                        {
                            "kind": "initial",
                            "schema_version": 1,
                            "config": config.model_dump(mode="json"),
                            "state_hash": simulation.state_hash(),
                            "agents": simulation.visualization_frame()["agents"],
                        }
                    )
                    + "\n"
                )
            for _ in range(args.ticks):
                report = simulation.advance()
                if log:
                    log.write(
                        json.dumps(
                            {
                                "kind": "tick",
                                "schema_version": 1,
                                "tick": report.tick,
                                "world_events": [
                                    asdict(e) for e in report.world_events
                                ],
                                "actions": [asdict(r) for r in report.action_results],
                                "intents": [asdict(i) for i in report.intents],
                                "life_events": [asdict(e) for e in report.life_events],
                            }
                        )
                        + "\n"
                    )
                if args.progress_every and (
                    (simulation.tick - start_tick) % args.progress_every == 0
                    or simulation.tick == start_tick + args.ticks
                ):
                    print(
                        f"tick {simulation.tick}/{start_tick + args.ticks} · "
                        f"population {len(simulation.agents)} · "
                        f"births {simulation.lifecycle.births} · "
                        f"deaths {simulation.lifecycle.deaths}",
                        file=sys.stderr,
                        flush=True,
                    )
            if args.checkpoint_out:
                save_checkpoint(simulation, args.checkpoint_out)
            if log:
                log.write(
                    json.dumps(
                        {
                            "kind": "complete",
                            "tick": simulation.tick,
                            "state_hash": simulation.state_hash(),
                        }
                    )
                    + "\n"
                )
        frame = simulation.visualization_update()
        print(
            json.dumps(
                {
                    "tick": simulation.tick,
                    "state_hash": simulation.state_hash(),
                    "agents": frame["agent_statistics"],
                },
                indent=2,
            )
        )
        return 0

    world = WorldBuilder.build(config.world)
    for _ in range(args.ticks):
        world.advance()
    world.snapshot().save(args.output)
    print(
        json.dumps(
            {
                "snapshot": str(args.output),
                "tick": world.tick,
                "state_hash": world.state_hash(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
