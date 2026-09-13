"""Command-line entry point for world generation and visualization."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from agentic_simulation.config import AppConfig, load_config
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config) if args.config.exists() else AppConfig()

    if args.command == "serve":
        host = args.host or config.visualization.host
        port = args.port or config.visualization.port
        uvicorn.run(create_app(config), host=host, port=port)
        return 0

    if args.ticks < 0:
        raise SystemExit("--ticks cannot be negative")
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
