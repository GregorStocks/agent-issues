"""Preflight for /solve-issue: verify the branch is ready to start new work.

Runs as a single invocation so agents that mangle multi-command `&&` chains
get one clean output stream. Checks:

1. working tree is clean
2. HEAD matches origin/<default-branch>
3. no open PR is already tied to the current branch
"""

import subprocess
import sys

from agent_issues.cli.common import default_branch


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def preflight() -> int:
    fetch = _run(["git", "fetch", "origin"])
    if fetch.returncode != 0:
        print(f"FAIL: git fetch origin: {fetch.stderr.strip()}")
        return 1

    default = default_branch()

    branch_result = _run(["git", "branch", "--show-current"])
    if branch_result.returncode != 0 or not branch_result.stdout.strip():
        print("FAIL: could not determine current branch")
        return 1
    current = branch_result.stdout.strip()

    print(f"current_branch={current}")
    print(f"default_branch={default}")

    status = _run(["git", "status", "--porcelain"])
    if status.returncode != 0:
        print(f"FAIL: git status: {status.stderr.strip()}")
        return 1
    if status.stdout.strip():
        print("FAIL: working tree has uncommitted changes:")
        print(status.stdout, end="")
        return 1

    head = _run(["git", "rev-parse", "HEAD"])
    base = _run(["git", "rev-parse", f"origin/{default}"])
    if head.returncode != 0 or base.returncode != 0:
        print("FAIL: git rev-parse failed")
        if head.stderr:
            print(head.stderr, end="")
        if base.stderr:
            print(base.stderr, end="")
        return 1
    head_sha = head.stdout.strip()
    base_sha = base.stdout.strip()
    if head_sha != base_sha:
        print(f"FAIL: HEAD ({head_sha}) does not match origin/{default} ({base_sha})")
        log = _run(["git", "log", "--oneline", f"{base_sha}..{head_sha}"])
        if log.stdout:
            print(log.stdout, end="")
        return 1

    open_pr = _run(
        ["gh", "pr", "list", "--head", current, "--state", "open", "--json", "number,title,url"]
    )
    if open_pr.returncode != 0:
        print(f"FAIL: gh pr list: {open_pr.stderr.strip()}")
        return 1
    body = open_pr.stdout.strip()
    if body and body != "[]":
        print("FAIL: current branch already has an open PR:")
        print(open_pr.stdout, end="")
        return 1

    print(f"OK: clean branch at origin/{default}, no open PR")
    return 0


def main() -> None:
    sys.exit(preflight())
