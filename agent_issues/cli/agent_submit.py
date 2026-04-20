"""Push HEAD, create or update the PR, and watch for CI+review outcomes."""

import argparse
import json
import subprocess
import sys
from typing import Sequence

from agent_issues.cli import issue_watch_pr

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
        url = create_result.stdout.strip()
        print(url, flush=True)
        pr_number = url.rsplit("/", 1)[-1]
        assert pr_number.isdigit(), f"could not parse PR number from gh pr create output: {url!r}"
        return pr_number

    pr_number = str(prs[0]["number"])
    edit_result = _run(["gh", "pr", "edit", pr_number, "--title", title, "--body", body])
    assert edit_result.returncode == 0, f"gh pr edit failed: {edit_result.stderr}"
    view_result = _run(["gh", "pr", "view", pr_number, "--json", "url", "--jq", ".url"])
    assert view_result.returncode == 0, f"gh pr view failed: {view_result.stderr}"
    print(view_result.stdout.strip(), flush=True)
    return pr_number


def _current_branch() -> str:
    result = _run(["git", "branch", "--show-current"])
    assert result.returncode == 0, f"git branch --show-current failed: {result.stderr}"
    branch = result.stdout.strip()
    assert branch, "Expected non-empty current branch"
    return branch


def _push(force: bool = False) -> int:
    """Push HEAD to origin. Returns git's exit code.

    When force=True, uses --force-with-lease. Safe here because preflight
    refuses to run on the default branch, so force only ever targets an
    agent-owned feature branch, and --force-with-lease still rejects the push
    if someone else updated the remote since we last fetched.
    """
    cmd = ["git", "push"]
    if force:
        cmd.append("--force-with-lease")
    cmd += ["origin", "HEAD"]
    return subprocess.run(cmd).returncode


def _print_next_step(code: int) -> None:
    if code == 0:
        return
    if code == 1:
        print(
            "\nNEXT STEP: CI failed or merge conflict. Investigate with `gh run view <run-id> "
            "--log-failed`, fix, then re-run `agent-submit`.",
            flush=True,
        )
    elif code == 2:
        print(
            "\nNEXT STEP: Review feedback received. Address the comments, then re-run `agent-submit`.",
            flush=True,
        )
    elif code == 4:
        print(
            "\nNEXT STEP: Watcher timed out — likely all fine but didn't confirm. "
            "Do not re-run automatically; stop and wait for the user.",
            flush=True,
        )


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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force-push with lease (after rebase or amend). Preflight still blocks the default branch.",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    code = preflight()
    if code != 0:
        sys.exit(code)

    branch = _current_branch()
    base = args.base if args.base is not None else _default_branch()

    push_code = _push(force=args.force)
    if push_code != 0:
        sys.exit(push_code)

    pr_number = upsert_pr(
        branch=branch, base=base, title=args.title, body=args.body, draft=args.draft
    )

    watcher_code = issue_watch_pr.run(pr=pr_number)
    _print_next_step(watcher_code)
    sys.exit(watcher_code)
