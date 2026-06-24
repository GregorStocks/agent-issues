"""CLI entry point for the shared PreToolUse hook runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from agent_issues.pretool_hook import DEFAULT_CONFIG_PATH, evaluate_hook_input, load_config


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate shared agent-issues PreToolUse hook policy.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help=f"JSON5 hook config path (default: {DEFAULT_CONFIG_PATH})",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    try:
        config = load_config(Path(args.config))
    except (OSError, ValueError) as exc:
        print(f"agent-pretool-hook: {exc}", file=sys.stderr)
        sys.exit(2)

    message = evaluate_hook_input(data, config)
    if message:
        print(message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
