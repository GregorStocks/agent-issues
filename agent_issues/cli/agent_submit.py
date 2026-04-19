"""Push HEAD, create or update the PR, and watch for CI+review outcomes."""

import argparse
import json
import subprocess
import sys
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


def upsert_pr(branch: str, base: str, title: str, body: str, draft: bool) -> str:
    """Create a PR if none exists on this branch, else edit the existing one.

    Returns the PR number as a string. Prints the PR URL.
    """
    list_result = _run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"]
    )
    assert list_result.returncode == 0, f"gh pr list failed: {list_result.stderr}"
    prs = json.loads(list_result.stdout) if list_result.stdout.strip() else []
    assert isinstance(prs, list), f"gh pr list returned non-list: {type(prs).__name__}"

    if len(prs) > 1:
        print(
            f"agent-submit: branch {branch} has more than one open PR ({len(prs)} found). "
            "Close the extras and retry.",
            flush=True,
        )
        sys.exit(EXIT_PREFLIGHT)

    if not prs:
        create_cmd = ["gh", "pr", "create", "--base", base, "--title", title, "--body", body]
        if draft:
            create_cmd.append("--draft")
        create_result = _run(create_cmd)
        assert create_result.returncode == 0, f"gh pr create failed: {create_result.stderr}"
        print(create_result.stdout.strip(), flush=True)
        number_result = _run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number"]
        )
        assert number_result.returncode == 0, f"gh pr list (post-create) failed: {number_result.stderr}"
        return number_result.stdout.strip()

    pr_number = str(prs[0]["number"])
    edit_result = _run(["gh", "pr", "edit", pr_number, "--title", title, "--body", body])
    assert edit_result.returncode == 0, f"gh pr edit failed: {edit_result.stderr}"
    view_result = _run(["gh", "pr", "view", pr_number, "--json", "url", "--jq", ".url"])
    if view_result.returncode == 0:
        print(view_result.stdout.strip(), flush=True)
    return pr_number


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
