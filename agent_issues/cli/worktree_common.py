"""Shared helpers for worktree CLI entrypoints."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from importlib import resources
from pathlib import Path


WORKTREE_BASE = Path.home() / "code" / "worktrees"


def log(msg: str) -> None:
    """Write an informational message to stderr."""
    print(msg, file=sys.stderr)


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    log(f"error: {msg}")
    sys.exit(code)


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, piping its stdout to our stderr so it doesn't contaminate our stdout."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        stdout=sys.stderr,
        text=True,
    )


def capture(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def require_git_repo() -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        die("not inside a git repository")


def git_common_dir() -> Path:
    """Path to the shared .git directory (works from any worktree)."""
    return Path(capture(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]))


def tombstone_dir() -> Path:
    return git_common_dir() / "worktree-tombstones"


def random_name() -> str:
    """Pick two words from the EFF short wordlist joined by a dash."""
    text = resources.files("agent_issues").joinpath("eff_wordlist.txt").read_text()
    words = [w for w in text.splitlines() if w]
    return f"{secrets.choice(words)}-{secrets.choice(words)}"


def tmux_rename_current_window(name: str) -> None:
    pane = os.environ.get("TMUX_PANE")
    if not os.environ.get("TMUX") or not pane:
        return
    subprocess.run(
        ["tmux", "rename-window", "-t", pane, name],
        check=False,
        stdout=sys.stderr,
    )


def tmux_enable_autorename_for(name: str) -> None:
    if not os.environ.get("TMUX"):
        return
    result = subprocess.run(
        ["tmux", "list-windows", "-F", "#{window_id} #{window_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return
    for line in result.stdout.splitlines():
        window_id, _, window_name = line.partition(" ")
        if window_name == name:
            subprocess.run(
                ["tmux", "set-option", "-w", "-t", window_id, "automatic-rename", "on"],
                check=False,
                stdout=sys.stderr,
            )
            break
