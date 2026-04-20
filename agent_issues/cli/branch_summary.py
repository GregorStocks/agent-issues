"""Summarize the current branch's unpushed work vs the default branch.

Used by /create-pr step 2 so the summary arrives in a single tool invocation
(some agents lose output before the last `&&` in a chained shell command).
"""

import subprocess
import sys

from agent_issues.cli.common import default_branch


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def summarize() -> int:
    fetch = _run(["git", "fetch", "origin"])
    if fetch.returncode != 0:
        print(f"FAIL: git fetch origin: {fetch.stderr.strip()}")
        return 1

    default = default_branch()
    base_ref = f"origin/{default}"

    print(f"=== commits ahead of {base_ref} ===")
    log = _run(["git", "log", "--oneline", f"{base_ref}..HEAD"])
    if log.returncode != 0:
        print(f"FAIL: git log: {log.stderr.strip()}")
        return 1
    sys.stdout.write(log.stdout)
    if log.stdout and not log.stdout.endswith("\n"):
        sys.stdout.write("\n")

    print()
    print(f"=== diff stat vs {base_ref} ===")
    diff = _run(["git", "diff", f"{base_ref}..HEAD", "--stat"])
    if diff.returncode != 0:
        print(f"FAIL: git diff: {diff.stderr.strip()}")
        return 1
    sys.stdout.write(diff.stdout)
    if diff.stdout and not diff.stdout.endswith("\n"):
        sys.stdout.write("\n")

    return 0


def main() -> None:
    sys.exit(summarize())
