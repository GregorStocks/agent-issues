"""Remove a git worktree, saving a tombstone so it can be restored later.

If a worktree name is not given, the current directory's worktree is used
(if it lives under the worktree base).

Prints a directory to `cd` to on stdout (the worktree's source root if we
were inside the worktree being removed, otherwise nothing). All progress
output goes to stderr.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agent_issues.cli.worktree_common import (
    WORKTREE_BASE,
    capture,
    die,
    log,
    require_git_repo,
    run,
    tmux_enable_autorename_for,
    tombstone_dir,
)


def main() -> None:
    require_git_repo()

    if len(sys.argv) > 1:
        name = sys.argv[1]
    else:
        cwd = Path.cwd().resolve()
        try:
            rel = cwd.relative_to(WORKTREE_BASE.resolve())
        except ValueError:
            die(f"specify a worktree name (or cd into one under {WORKTREE_BASE})")
        name = rel.parts[0]

    worktree_path = WORKTREE_BASE / name
    if not worktree_path.is_dir():
        die(f"worktree not found: {worktree_path}")

    source_root = Path(
        capture(
            ["git", "-C", str(worktree_path), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
    ).parent

    # Record the tip so worktree-unrm can restore it.
    tombstones = tombstone_dir()
    tombstones.mkdir(parents=True, exist_ok=True)
    head = capture(["git", "-C", str(worktree_path), "rev-parse", "HEAD"])
    (tombstones / name).write_text(head + "\n")

    cwd = Path.cwd().resolve()
    victim = worktree_path.resolve()
    inside_victim = cwd == victim or victim in cwd.parents
    if inside_victim:
        # Can't operate on git from a directory about to be deleted.
        os.chdir(source_root)

    run(["git", "worktree", "remove", str(worktree_path)])
    subprocess.run(
        ["git", "branch", "-d", name],
        stdout=sys.stderr,
        stderr=subprocess.STDOUT,
        check=False,
    )

    tmux_enable_autorename_for(name)

    log(f"Removed worktree: {worktree_path}")

    # If the shell invoked us from the worktree we just deleted, emit the
    # source root so the shell wrapper can cd there.
    if inside_victim:
        print(source_root)
