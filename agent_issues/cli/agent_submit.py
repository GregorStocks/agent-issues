"""Push HEAD, create or update the PR, and watch for CI+review outcomes."""

import argparse
import subprocess
from typing import Sequence

EXIT_PREFLIGHT = 10


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def _default_branch() -> str:
    # Duplicates common.default_branch() — kept local so every subprocess call in
    # this module flows through _run, which is the single test seam for mocking.
    result = _run(
        ["gh", "repo", "view", "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"]
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "main"


def preflight() -> int:
    """Run all preflight checks. Returns 0 if clean, EXIT_PREFLIGHT otherwise."""
    inside = _run(["git", "rev-parse", "--is-inside-work-tree"])
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        print("agent-submit: not in a git repository.", flush=True)
        return EXIT_PREFLIGHT

    branch_result = _run(["git", "branch", "--show-current"])
    assert branch_result.returncode == 0, f"git branch --show-current failed: {branch_result.stderr}"
    branch = branch_result.stdout.strip()
    assert branch, "Expected non-empty current branch"

    default = _default_branch()
    if branch == default:
        print(
            f"agent-submit: refusing to push — HEAD is on the default branch ({default}). "
            "Create a feature branch first.",
            flush=True,
        )
        return EXIT_PREFLIGHT

    status = _run(["git", "status", "--porcelain"])
    assert status.returncode == 0, f"git status failed: {status.stderr}"
    if status.stdout.strip():
        print(
            "agent-submit: refusing to push — uncommitted changes in working tree. "
            "Commit or stash them first.",
            flush=True,
        )
        return EXIT_PREFLIGHT

    return 0


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
