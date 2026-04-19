"""Push HEAD, create or update the PR, and watch for CI+review outcomes."""

import argparse
from typing import Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Push HEAD, create or update the PR, and run the CI watcher.",
    )
    parser.add_argument("--title", required=True, help="PR title")
    parser.add_argument("--body", required=True, help="PR body")
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Create PR as draft (ignored on update).",
    )
    parser.add_argument(
        "--base",
        default=None,
        help="Base branch for new PRs (default: repo's default branch).",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    raise NotImplementedError(args)
