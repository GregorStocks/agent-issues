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
