"""Tests for agent_issues.cli.issue_watch_pr."""

import json
import sys
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from agent_issues.cli import issue_watch_pr


def _gh_result(stdout: str, returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_get_pr_lifecycle_state_reports_merged() -> None:
    with patch.object(
        issue_watch_pr,
        "run_gh",
        return_value=_gh_result(json.dumps({"state": "CLOSED", "mergedAt": "2026-04-15T01:02:03Z"})),
    ):
        assert issue_watch_pr.get_pr_lifecycle_state("123") == "merged"


def test_main_exits_0_when_pr_is_already_merged(capsys) -> None:
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="merged"),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "Watching PR #123...\n\nPR has been merged.\n"


def test_main_exits_0_when_pr_merges_while_waiting_for_checks(capsys) -> None:
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=[]),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", side_effect=["open", "merged"]),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0
    assert capsys.readouterr().out == "Watching PR #123...\n\nPR has been merged.\n"
