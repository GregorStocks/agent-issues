"""Restore a worktree previously removed by worktree-rm.

Prints the absolute path of the restored worktree on stdout.
"""

from __future__ import annotations

import sys

from agent_issues.cli.worktree_common import (
    WORKTREE_BASE,
    die,
    log,
    require_git_repo,
    run,
    tmux_rename_current_window,
    tombstone_dir,
)


def main() -> None:
    require_git_repo()

    if len(sys.argv) < 2:
        die("usage: worktree-unrm <branch-name>", code=2)
    name = sys.argv[1]

    tombstone = tombstone_dir() / name
    if not tombstone.is_file():
        die(f"no tombstone found for '{name}'")
    commit = tombstone.read_text().strip()

    worktree_path = WORKTREE_BASE / name
    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", str(worktree_path), "-b", name, commit])
    tombstone.unlink()

    tmux_rename_current_window(name)

    log(f"Restored worktree: {worktree_path}")
    print(worktree_path)
