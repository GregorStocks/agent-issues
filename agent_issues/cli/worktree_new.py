"""Create a new git worktree branched off origin/master.

Prints the absolute path of the new worktree on stdout; all other output
goes to stderr so `cd "$(worktree-new)"` works in a shell wrapper.
"""

from __future__ import annotations

import os
import sys

from agent_issues.cli.worktree_common import (
    WORKTREE_BASE,
    log,
    random_name,
    require_git_repo,
    run,
    tmux_rename_current_window,
)


def main() -> None:
    require_git_repo()

    branch_name = sys.argv[1] if len(sys.argv) > 1 else random_name()
    worktree_path = WORKTREE_BASE / branch_name

    WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

    run(["git", "fetch", "origin", "master"])
    run(["git", "worktree", "add", str(worktree_path), "-b", branch_name, "origin/master"])

    tmux_rename_current_window(branch_name)

    setup_script = worktree_path / "scripts" / "worktree-setup.py"
    if setup_script.is_file() and os.access(setup_script, os.X_OK):
        run([str(setup_script)], cwd=worktree_path)

    log(f"Created worktree: {worktree_path}")
    print(worktree_path)
