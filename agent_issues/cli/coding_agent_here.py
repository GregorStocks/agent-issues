"""Start a coding agent in the current worktree, creating one when needed."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_issues.cli.worktree_common import capture, die


def in_git_repo() -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def in_linked_worktree() -> bool:
    git_dir = Path(capture(["git", "rev-parse", "--path-format=absolute", "--git-dir"]))
    common_dir = Path(capture(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]))
    return git_dir != common_dir


def current_repo_relative_dir() -> Path:
    repo_root = Path(capture(["git", "rev-parse", "--path-format=absolute", "--show-toplevel"])).resolve()
    return Path.cwd().resolve().relative_to(repo_root)


def launch_dir() -> Path | None:
    if not in_git_repo() or in_linked_worktree():
        return None

    relative_dir = current_repo_relative_dir()
    try:
        result = subprocess.run(
            ["worktree-new"],
            stdout=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        die("worktree-new not found on PATH", code=127)

    if result.returncode != 0:
        raise SystemExit(result.returncode)

    target_root_text = result.stdout.strip()
    if not target_root_text:
        die("worktree-new did not print a target path")

    target_root = Path(target_root_text).resolve()
    target_dir = target_root / relative_dir
    return target_dir if target_dir.exists() else target_root


def main(argv: list[str] | None = None) -> None:
    agent_argv = list(sys.argv[1:] if argv is None else argv)
    if not agent_argv:
        die("usage: coding-agent-here <agent> [args...]", code=2)

    target_dir = launch_dir()
    if target_dir is not None:
        os.chdir(target_dir)

    try:
        os.execvp(agent_argv[0], agent_argv)
    except FileNotFoundError:
        die(f"command not found: {agent_argv[0]}", code=127)
