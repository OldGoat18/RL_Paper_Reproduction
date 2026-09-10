"""Command-line interface for the RL reproduction harness."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .detector import detect_output_paths
from .runner import run_project
from .web import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rl-harness", description="Run RL projects and expose their native output locations.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    detect = subparsers.add_parser("detect", help="Detect output paths from project files")
    detect.add_argument("project", nargs="?", default=".")
    detect.add_argument("--llm", action="store_true")

    run = subparsers.add_parser("run", help="Run a project command and record execution metadata")
    run.add_argument("project", nargs="?", default=".")
    run.add_argument("--task", default="train")
    run.add_argument("--seed", type=int)
    run.add_argument("--repeat", type=int)
    run.add_argument("--metadata-root", default=".harness")
    run.add_argument("--output", dest="output_override", help="Manually override the detected output path")
    web = subparsers.add_parser("web", help="Serve a local execution metadata viewer")
    web.add_argument("--metadata-root", default=".harness")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--project", action="append", default=[])
    return parser


def _main(argv: Optional[List[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    command: List[str] = []
    # Split at the conventional delimiter before argparse sees the project's
    # command, so command flags cannot be mistaken for harness flags.
    if "--" in raw_argv:
        delimiter = raw_argv.index("--")
        command = raw_argv[delimiter + 1 :]
        raw_argv = raw_argv[:delimiter]
    args = build_parser().parse_args(raw_argv)
    if args.subcommand == "detect":
        from .llm import detect_with_llm
        print(json.dumps((detect_with_llm if args.llm else detect_output_paths)(args.project, command).to_dict(), indent=2))
        return 0
    if args.subcommand == "web":
        serve(args.metadata_root, args.host, args.port, args.project)
        return 0
    if not command:
        print("run requires a command (for example: rl-harness run . -- python train.py)", file=sys.stderr)
        return 2
    metadata, detection, exit_code = run_project(
        args.project,
        command,
        task=args.task,
        seed=args.seed,
        repeat=args.repeat,
        metadata_root=args.metadata_root,
        output_override=args.output_override,
    )
    print(json.dumps({"execution": metadata.to_dict(), "detection": detection.to_dict()}, indent=2))
    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return _main(argv)
    except (OSError, ValueError) as exc:
        print(f"rl-harness: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
