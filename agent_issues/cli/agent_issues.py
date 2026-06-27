"""Umbrella CLI for agent-issues commands."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from agent_issues.cli import init_repo


def main(argv: Sequence[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "init":
        init_repo.main(argv[1:])
        return

    parser = argparse.ArgumentParser(prog="agent-issues")
    parser.add_argument("command", choices=["init"], help="command to run")
    parser.parse_args(argv)


if __name__ == "__main__":
    main()
