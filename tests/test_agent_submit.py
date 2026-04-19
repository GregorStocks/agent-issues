"""Tests for agent_issues.cli.agent_submit."""

import sys
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from agent_issues.cli import agent_submit


def _result(stdout: str = "", returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_requires_title_and_body() -> None:
    with patch.object(sys, "argv", ["agent-submit"]), pytest.raises(SystemExit):
        agent_submit.main()


def test_parses_title_and_body() -> None:
    args = agent_submit.parse_args(["--title", "T", "--body", "B"])
    assert args.title == "T"
    assert args.body == "B"
    assert args.draft is False
    assert args.base is None


def test_parses_optional_flags() -> None:
    args = agent_submit.parse_args(
        ["--title", "T", "--body", "B", "--draft", "--base", "develop"]
    )
    assert args.draft is True
    assert args.base == "develop"


def test_preflight_fails_when_not_in_git_repo(capsys) -> None:
    with patch.object(agent_submit, "_run", return_value=_result(returncode=128, stderr="not a git repo")):
        code = agent_submit.preflight()
    assert code == 10
    assert "not in a git repository" in capsys.readouterr().out.lower()


def test_preflight_fails_on_default_branch(capsys) -> None:
    # First _run call: git rev-parse --is-inside-work-tree -> ok
    # Second: git branch --show-current -> "main"
    # Third: gh repo view ... -> "main"
    results = [
        _result(stdout="true\n"),
        _result(stdout="main\n"),
        _result(stdout="main\n"),
    ]
    with patch.object(agent_submit, "_run", side_effect=results):
        code = agent_submit.preflight()
    assert code == 10
    assert "default branch" in capsys.readouterr().out.lower()


def test_preflight_fails_on_dirty_tree(capsys) -> None:
    results = [
        _result(stdout="true\n"),
        _result(stdout="feature-x\n"),
        _result(stdout="main\n"),
        _result(stdout=" M file.py\n"),  # porcelain non-empty
    ]
    with patch.object(agent_submit, "_run", side_effect=results):
        code = agent_submit.preflight()
    assert code == 10
    assert "uncommitted" in capsys.readouterr().out.lower()


def test_preflight_passes_on_clean_feature_branch() -> None:
    results = [
        _result(stdout="true\n"),
        _result(stdout="feature-x\n"),
        _result(stdout="main\n"),
        _result(stdout=""),  # porcelain empty
    ]
    with patch.object(agent_submit, "_run", side_effect=results):
        code = agent_submit.preflight()
    assert code == 0
