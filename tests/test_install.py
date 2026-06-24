"""Tests for the installer shell script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_fake_uv(bin_dir: Path) -> None:
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
if [ "$1" = "tool" ] && [ "$2" = "install" ]; then
    exit 0
fi
if [ "$1" = "tool" ] && [ "$2" = "dir" ] && [ "$3" = "--bin" ]; then
    echo "$HOME/.local/bin"
    exit 0
fi
echo "unexpected uv command: $*" >&2
exit 64
"""
    )
    uv.chmod(0o755)


def _run_install(repo_root: Path, home: Path, bin_dir: Path) -> None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"

    subprocess.run(
        ["bash", str(repo_root / "install.sh")],
        check=True,
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
    )


def test_install_adds_global_agent_issues_guidance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    _run_install(repo_root, home, bin_dir)

    claude_include = f"@{home}/.claude/skills/agent-issues/SKILL.md"
    codex_guidance = (
        "Use the `agent-issues` skill when working in repositories that use the "
        "agent-issues local issue workflow."
    )

    assert (home / ".claude/CLAUDE.md").read_text() == f"{claude_include}\n"
    assert (home / ".codex/AGENTS.md").read_text() == f"{codex_guidance}\n"
    assert (home / ".claude/skills/agent-issues").is_symlink()
    assert (home / ".codex/skills/agent-issues").is_symlink()


def test_install_preserves_existing_global_agent_content(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_uv(bin_dir)

    claude_file = home / ".claude/CLAUDE.md"
    codex_file = home / ".codex/AGENTS.md"
    claude_file.parent.mkdir(parents=True)
    codex_file.parent.mkdir(parents=True)
    claude_file.write_text("Existing Claude instructions.\n")
    codex_file.write_text("Existing Codex instructions.\n")

    _run_install(repo_root, home, bin_dir)
    _run_install(repo_root, home, bin_dir)

    claude_include = f"@{home}/.claude/skills/agent-issues/SKILL.md"
    codex_guidance = (
        "Use the `agent-issues` skill when working in repositories that use the "
        "agent-issues local issue workflow."
    )

    claude_text = claude_file.read_text()
    codex_text = codex_file.read_text()

    assert claude_text == f"Existing Claude instructions.\n\n{claude_include}\n"
    assert codex_text == f"Existing Codex instructions.\n\n{codex_guidance}\n"
    assert claude_text.count(claude_include) == 1
    assert codex_text.count(codex_guidance) == 1
