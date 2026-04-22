"""Tests for agent_issues.cli.coding_agent_here."""

from __future__ import annotations

import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from agent_issues.cli import coding_agent_here


def _result(
    args: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> CompletedProcess[str]:
    return CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def test_usage_error_without_command() -> None:
    with pytest.raises(SystemExit, match="2"):
        coding_agent_here.main([])


def test_runs_agent_in_place_outside_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_run(cmd: list[str], **kwargs) -> CompletedProcess[str]:
        assert cmd == ["git", "rev-parse", "--is-inside-work-tree"]
        return _result(cmd, returncode=128)

    invoked: dict[str, object] = {}

    def fake_execvp(file: str, args: list[str]) -> None:
        invoked["file"] = file
        invoked["args"] = args
        invoked["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(coding_agent_here.subprocess, "run", fake_run)
    monkeypatch.setattr(coding_agent_here.os, "execvp", fake_execvp)

    with pytest.raises(SystemExit, match="0"):
        coding_agent_here.main(["codex", "--model", "gpt-5.4"])

    assert invoked == {
        "file": "codex",
        "args": ["codex", "--model", "gpt-5.4"],
        "cwd": str(tmp_path),
    }


def test_runs_agent_in_place_when_already_on_linked_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree_root = tmp_path / "worktree"
    subdir = worktree_root / "src"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    def fake_capture(cmd: list[str], **kwargs) -> str:
        if cmd == ["git", "rev-parse", "--path-format=absolute", "--git-dir"]:
            return str(tmp_path / ".git" / "worktrees" / "worktree")
        if cmd == ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"]:
            return str(tmp_path / ".git")
        raise AssertionError(f"unexpected capture: {cmd}")

    def fake_run(cmd: list[str], **kwargs) -> CompletedProcess[str]:
        assert cmd == ["git", "rev-parse", "--is-inside-work-tree"]
        return _result(cmd, stdout="true\n")

    invoked: dict[str, object] = {}

    def fake_execvp(file: str, args: list[str]) -> None:
        invoked["file"] = file
        invoked["args"] = args
        invoked["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(coding_agent_here, "capture", fake_capture)
    monkeypatch.setattr(coding_agent_here.subprocess, "run", fake_run)
    monkeypatch.setattr(coding_agent_here.os, "execvp", fake_execvp)

    with pytest.raises(SystemExit, match="0"):
        coding_agent_here.main(["claude", "--permission-mode=auto"])

    assert invoked == {
        "file": "claude",
        "args": ["claude", "--permission-mode=auto"],
        "cwd": str(subdir),
    }


def test_creates_new_worktree_from_main_checkout_and_preserves_subdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    subdir = repo_root / "pkg" / "module"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    new_worktree_root = tmp_path / "worktrees" / "fresh-branch"
    (new_worktree_root / "pkg" / "module").mkdir(parents=True)

    def fake_capture(cmd: list[str], **kwargs) -> str:
        mapping = {
            ("git", "rev-parse", "--path-format=absolute", "--git-dir"): str(repo_root / ".git"),
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"): str(repo_root / ".git"),
            ("git", "rev-parse", "--path-format=absolute", "--show-toplevel"): str(repo_root),
        }
        try:
            return mapping[tuple(cmd)]
        except KeyError as exc:
            raise AssertionError(f"unexpected capture: {cmd}") from exc

    def fake_run(cmd: list[str], **kwargs) -> CompletedProcess[str]:
        if cmd == ["git", "rev-parse", "--is-inside-work-tree"]:
            return _result(cmd, stdout="true\n")
        if cmd == ["worktree-new"]:
            return _result(cmd, stdout=f"{new_worktree_root}\n")
        raise AssertionError(f"unexpected run: {cmd}")

    invoked: dict[str, object] = {}

    def fake_execvp(file: str, args: list[str]) -> None:
        invoked["file"] = file
        invoked["args"] = args
        invoked["cwd"] = os.getcwd()
        raise SystemExit(0)

    monkeypatch.setattr(coding_agent_here, "capture", fake_capture)
    monkeypatch.setattr(coding_agent_here.subprocess, "run", fake_run)
    monkeypatch.setattr(coding_agent_here.os, "execvp", fake_execvp)

    with pytest.raises(SystemExit, match="0"):
        coding_agent_here.main(["codex"])

    assert invoked == {
        "file": "codex",
        "args": ["codex"],
        "cwd": str(new_worktree_root / "pkg" / "module"),
    }


def test_exits_with_worktree_new_status_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.chdir(repo_root)

    def fake_capture(cmd: list[str], **kwargs) -> str:
        mapping = {
            ("git", "rev-parse", "--path-format=absolute", "--git-dir"): str(repo_root / ".git"),
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"): str(repo_root / ".git"),
            ("git", "rev-parse", "--path-format=absolute", "--show-toplevel"): str(repo_root),
        }
        try:
            return mapping[tuple(cmd)]
        except KeyError as exc:
            raise AssertionError(f"unexpected capture: {cmd}") from exc

    def fake_run(cmd: list[str], **kwargs) -> CompletedProcess[str]:
        if cmd == ["git", "rev-parse", "--is-inside-work-tree"]:
            return _result(cmd, stdout="true\n")
        if cmd == ["worktree-new"]:
            return _result(cmd, returncode=7)
        raise AssertionError(f"unexpected run: {cmd}")

    monkeypatch.setattr(coding_agent_here, "capture", fake_capture)
    monkeypatch.setattr(coding_agent_here.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="7"):
        coding_agent_here.main(["codex"])
