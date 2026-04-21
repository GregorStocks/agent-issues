"""Tests for agent_issues.cli.issue_watch_pr."""

import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from agent_issues.cli import issue_watch_pr


def _gh_result(stdout: str, returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def _feedback(formatted: str, seconds_old: float = 0.0) -> dict:
    now = datetime.now(timezone.utc)
    return {"formatted": formatted, "created_at": now - timedelta(seconds=seconds_old)}


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


def test_main_exits_1_on_merge_conflict(capsys) -> None:
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=True),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 1
    assert "merge conflict" in capsys.readouterr().out


def test_main_exits_1_when_any_check_fails(capsys) -> None:
    failing_checks = [
        {"name": "lint", "bucket": "pass", "link": "l"},
        {"name": "test", "bucket": "fail", "link": "https://example.com/run/42"},
    ]
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=failing_checks),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "1 check(s) FAILED" in out
    assert "test (fail)" in out


def test_main_exits_2_when_new_comment_is_older_than_grace(capsys) -> None:
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    fresh_feedback = [_feedback("[COMMENT] @codex: please fix", seconds_old=25)]
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(
            issue_watch_pr,
            "get_review_feedback",
            side_effect=[[], fresh_feedback],
        ),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=[]),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "1 new review comment(s)" in out
    assert "please fix" in out


def test_main_does_not_exit_while_new_comment_is_younger_than_grace(capsys) -> None:
    """Comment younger than COMMENT_GRACE should not trigger exit on that tick."""
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    young = [_feedback("[COMMENT] @codex: please fix", seconds_old=2)]
    old = [_feedback("[COMMENT] @codex: please fix", seconds_old=25)]
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(
            issue_watch_pr,
            "get_review_feedback",
            side_effect=[[], young, old],
        ),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=[]),
        patch.object(issue_watch_pr.time, "sleep") as mock_sleep,
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 2
    # We should have slept at least once (first tick saw young comment, looped)
    assert mock_sleep.call_count >= 1


def test_main_exits_0_after_15_min_with_no_eyes(capsys) -> None:
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    # monotonic returns start (0.0) for the initial call, then 16 min later
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=[]),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", side_effect=[0.0, 16 * 60]),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "No codex review after 15 min" in out


def test_main_does_not_exit_when_plus_one_but_ci_still_pending() -> None:
    """+1 alone must not short-circuit while CI is still running."""
    pending_checks = [{"name": "build", "bucket": "pending", "link": "l"}]
    plus_one = [{"content": "+1"}]
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(
            issue_watch_pr,
            "get_pr_lifecycle_state",
            side_effect=["open", "open", "merged"],
        ),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=pending_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=plus_one),
        patch.object(issue_watch_pr.time, "sleep") as mock_sleep,
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0  # exits on "merged" via lifecycle check
    # Must have looped at least once rather than exiting immediately on +1
    assert mock_sleep.call_count >= 1


def test_main_exits_0_when_ci_passed_and_plus_one(capsys) -> None:
    passing_checks = [
        {"name": "lint", "bucket": "pass", "link": "l"},
        {"name": "test", "bucket": "pass", "link": "l"},
    ]
    plus_one = [{"content": "+1"}]
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=plus_one),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Codex approved" in out


def test_run_writes_detailed_log_when_logs_dir_exists(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()

    passing_checks = [
        {"name": "lint", "bucket": "pass", "link": "l"},
        {"name": "test", "bucket": "pass", "link": "l"},
    ]
    plus_one = [{"content": "+1"}]

    with (
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=plus_one),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
    ):
        previous_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            code = issue_watch_pr.run("123")
        finally:
            os.chdir(previous_cwd)

    assert code == 0
    log_files = sorted(logs_dir.glob("agent-submit-*.log"))
    assert len(log_files) == 1
    content = log_files[0].read_text()
    assert "INFO" in content
    assert "starting watcher pr=123 repo=owner/repo" in content
    assert "poll=1 observed elapsed=0.0s" in content
    assert "plus_one=True" in content
    assert "exiting clean with codex approval" in content
