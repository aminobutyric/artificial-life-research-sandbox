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
from agentic_simulation.simulation import SimulationBuilder
from agentic_simulation.visualization import create_app
from agentic_simulation.world import WorldBuilder


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alife")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/world.yaml"),
        help="YAML configuration path",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="serve the live world visualization")
    serve.add_argument("--host", help="override the configured bind address")
    serve.add_argument("--port", type=int, help="override the configured port")

    generate = commands.add_parser(
        "generate", help="run headlessly and save a snapshot"
    )
    generate.add_argument("--ticks", type=int, default=0)
    generate.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="run agents headlessly")
    run.add_argument("--ticks", type=int, required=True)
    run.add_argument("--events", type=Path, help="write a new buffered JSONL event log")
    run.add_argument(
        "--progress-every",
        type=int,
        default=50,
        metavar="TICKS",
        help="report progress to stderr every N ticks; use 0 to disable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)

    if args.command == "serve":
        host = args.host or config.visualization.host
        port = args.port or config.visualization.port
        print(
            f"Browser visualization: http://{host}:{port}\n"
            "Starts paused. Open the address, then click Play or Run & pause.",
            file=sys.stderr,
            flush=True,
        )
        uvicorn.run(create_app(config), host=host, port=port)
        return 0

    if args.ticks < 0:
        raise SystemExit("--ticks cannot be negative")
    if args.command == "run":
        if args.progress_every < 0:
            raise SystemExit("--progress-every cannot be negative")
        if args.progress_every:
            print(
                f"Starting headless simulation for {args.ticks} ticks.\n"
                "For the browser visualization, use 'alife --config "
                f"{args.config} serve'.",
                file=sys.stderr,
                flush=True,
            )
        simulation = SimulationBuilder.build(config)
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
                    simulation.tick % args.progress_every == 0
                    or simulation.tick == args.ticks
                ):
                    print(
                        f"tick {simulation.tick}/{args.ticks} · "
                        f"population {len(simulation.agents)} · "
                        f"births {simulation.lifecycle.births} · "
                        f"deaths {simulation.lifecycle.deaths}",
                        file=sys.stderr,
                        flush=True,
                    )
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
