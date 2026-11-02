"""Tests for agent_issues.cli.issue_watch_pr."""

import json
import os
from contextlib import ExitStack, contextmanager
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from agent_issues.cli import issue_watch_pr


def _gh_result(stdout: str, returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def _feedback(formatted: str, seconds_old: float = 0.0, id: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": id if id is not None else f"test:{formatted}",
        "formatted": formatted,
        "created_at": now - timedelta(seconds=seconds_old),
    }


HEAD_SHA = "922996bda2f1ad48e7f97afb5a67961a303b2f1c"
OLD_SHA = "b2df3a9afb75bc8eb45785bbb2bcdaf68ec8435a"
HEAD_COMMITTED_AT = datetime(2026, 11, 2, 0, 12, 49, tzinfo=timezone.utc)
PR_AUTHOR = "GregorStocks"
CODEX_LOGIN = "chatgpt-codex-connector[bot]"


# Unpatched helpers, captured before the autouse fixture replaces them.
_real_get_pr_head = issue_watch_pr.get_pr_head
_real_get_codex_review_state = issue_watch_pr.get_codex_review_state


@pytest.fixture(autouse=True)
def _default_head_and_no_codex_summary():
    """Most tests exercise the reaction fallback: a fixed head and no summary comment."""
    head = {"sha": HEAD_SHA, "committed_at": HEAD_COMMITTED_AT, "author": PR_AUTHOR}
    with (
        patch.object(issue_watch_pr, "get_pr_head", return_value=head),
        patch.object(issue_watch_pr, "get_codex_review_state", return_value=None),
    ):
        yield


def _plus_one(created_at: str = "2026-11-02T00:56:49Z") -> dict:
    return {"content": "+1", "created_at": created_at, "login": CODEX_LOGIN}


def _summary_body(
    commit_sha: str = HEAD_SHA,
    status: str | None = "completed",
    has_any_findings: bool | None = False,
    has_unresolved_findings: bool | None = False,
    security: dict | None = None,
) -> str:
    """A Codex Review Summary comment body, modelled on block-descent PR #249."""
    state = {
        "code_review": {
            "commit_sha": commit_sha,
            "has_any_findings": has_any_findings,
            "has_unresolved_findings": has_unresolved_findings,
            "status": status,
        },
        "security_review": security
        or {"commit_sha": None, "has_any_findings": None, "has_unresolved_findings": None, "status": None},
    }
    return (
        "<!-- codex-pull-request-review-summary -->\n"
        f"<!-- codex-review-state:v1 {json.dumps(state, separators=(',', ':'))} -->\n\n"
        "## Codex Review Summary\n\n"
        "This comment shows the latest Codex review activity on this pull request.\n\n"
        "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
        f"| 📝 **Code Review** | ✅ **Completed** | `{commit_sha[:7]}` | New commits |\n"
    )


def _codex_state(seconds_old: float = 120.0, **kwargs) -> dict:
    state = issue_watch_pr.parse_codex_review_state(_summary_body(**kwargs))
    assert state is not None
    state["updated_at"] = datetime.now(timezone.utc) - timedelta(seconds=seconds_old)
    return state


def _finding(title: str) -> str:
    return (
        "**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  "
        f"{title}**\n\nDetails of the finding.\n\nUseful? React with 👍 / 👎.\n\n"
        "<!-- codex-code-review-finding:v1 -->"
    )


def _thread(title: str, *, replied: bool, outdated: bool = False, resolved: bool = False) -> dict:
    comments = [
        {
            "author": {"login": "chatgpt-codex-connector"},
            "body": _finding(title),
            "createdAt": "2026-11-01T23:02:36Z",
            "url": "https://github.com/o/r/pull/249#discussion_r1",
        }
    ]
    if replied:
        comments.append(
            {
                "author": {"login": PR_AUTHOR},
                "body": "Fixed in 660badb0.",
                "createdAt": "2026-11-01T23:11:37Z",
                "url": "https://github.com/o/r/pull/249#discussion_r2",
            }
        )
    return {
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": "crates/core/src/campaign.rs",
        "line": None if outdated else 1198,
        "comments": {"nodes": comments},
    }


def _threads_payload(*threads: dict) -> str:
    return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": list(threads)}}}}})


@contextmanager
def _watch(*, checks, codex_state=None, reactions=(), feedback=(), threads=None, lifecycle=None):
    """Patch the watcher's gh-backed helpers for a single-poll scenario."""
    patches = [
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(
            issue_watch_pr,
            "get_pr_lifecycle_state",
            **({"side_effect": lifecycle} if lifecycle else {"return_value": "open"}),
        ),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=list(feedback)),
        patch.object(issue_watch_pr, "get_checks", return_value=checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=list(reactions)),
        patch.object(issue_watch_pr, "get_codex_review_state", return_value=codex_state),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
    ]
    if threads is not None:
        patches.append(
            patch.object(
                issue_watch_pr,
                "run_gh",
                side_effect=lambda *args: _gh_result(_threads_payload(*threads)),
            )
        )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        mock_sleep = stack.enter_context(patch.object(issue_watch_pr.time, "sleep"))
        yield mock_sleep


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


def test_main_exits_0_after_45_min_with_no_eyes(capsys) -> None:
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    # monotonic returns start (0.0) for the initial call, then slightly after 45 min
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=[]),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", side_effect=[0.0, 45 * 60 + 1]),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "No codex review after 45 min" in out


def test_main_exits_4_with_retry_guidance_after_overall_timeout(capsys) -> None:
    pending_checks = [{"name": "lint", "bucket": "pending", "link": "l"}]
    eyes = [{"content": "eyes"}]
    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[]),
        patch.object(issue_watch_pr, "get_checks", return_value=pending_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=eyes),
        patch.object(issue_watch_pr.time, "monotonic", side_effect=[0.0, issue_watch_pr.TIMEOUT + 1]),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 4
    out = capsys.readouterr().out
    assert "Timed out" in out
    assert "consult the user" in out
    assert "feedback that arrived while stopped" in out
    assert "re-run agent-submit" in out
    assert "manual PR watching" in out


def test_relocated_inline_comment_does_not_count_as_new(capsys) -> None:
    """An old inline comment relocated by GitHub after a force-push has the same id
    but a different formatted string; tracking by id keeps it out of new_feedback."""
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    plus_one = [_plus_one()]
    baseline = [_feedback("[INLINE] @codex on file.py:10: please fix", id="inline:42", seconds_old=600)]
    relocated = [_feedback("[INLINE] @codex on file.py:12: please fix", id="inline:42", seconds_old=600)]

    with (
        patch.object(sys, "argv", ["issue-watch-pr", "123"]),
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(
            issue_watch_pr,
            "get_review_feedback",
            side_effect=[baseline, relocated],
        ),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=plus_one),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
        pytest.raises(SystemExit) as excinfo,
    ):
        issue_watch_pr.main()

    assert excinfo.value.code == 0
    assert "new review comment" not in capsys.readouterr().out


def test_main_does_not_exit_when_plus_one_but_ci_still_pending() -> None:
    """+1 alone must not short-circuit while CI is still running."""
    pending_checks = [{"name": "build", "bucket": "pending", "link": "l"}]
    plus_one = [_plus_one()]
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
    plus_one = [_plus_one()]
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
    plus_one = [_plus_one()]

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


def test_run_reports_feedback_missing_from_feedback_seen(capsys) -> None:
    """Feedback that arrived after the caller's snapshot is new, even if it already
    exists when the watcher starts (for example, posted during after_publish hooks)."""
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    old = _feedback("[COMMENT] @codex: old nit", id="comment:1", seconds_old=600)
    during_hooks = _feedback("[COMMENTED] @codex: new bug", id="review:2", seconds_old=120)

    with (
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[old, during_hooks]),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=[]),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
    ):
        code = issue_watch_pr.run("123", feedback_seen={"comment:1"})

    assert code == 2
    out = capsys.readouterr().out
    assert "1 new review comment(s)" in out
    assert "new bug" in out
    assert "old nit" not in out


def test_run_ignores_feedback_in_feedback_seen(capsys) -> None:
    """On a resubmit, feedback addressed earlier is in the snapshot and stays ignored."""
    passing_checks = [{"name": "lint", "bucket": "pass", "link": "l"}]
    plus_one = [_plus_one()]
    old = _feedback("[COMMENT] @codex: old nit", id="comment:1", seconds_old=600)

    with (
        patch.object(issue_watch_pr, "get_repo_nwo", return_value="owner/repo"),
        patch.object(issue_watch_pr, "get_pr_lifecycle_state", return_value="open"),
        patch.object(issue_watch_pr, "check_merge_conflict", return_value=False),
        patch.object(issue_watch_pr, "get_review_feedback", return_value=[old]),
        patch.object(issue_watch_pr, "get_checks", return_value=passing_checks),
        patch.object(issue_watch_pr, "get_pr_reactions", return_value=plus_one),
        patch.object(issue_watch_pr.time, "sleep"),
        patch.object(issue_watch_pr.time, "monotonic", return_value=0.0),
    ):
        code = issue_watch_pr.run("123", feedback_seen={"comment:1"})

    assert code == 0
    assert "new review comment" not in capsys.readouterr().out


PASSING = [{"name": "check", "bucket": "pass", "link": "l"}]


def test_summary_completed_for_head_exits_0_without_plus_one(capsys) -> None:
    """block-descent PR #251: Codex completed the head with no findings."""
    with _watch(checks=PASSING, codex_state=_codex_state()) as mock_sleep:
        code = issue_watch_pr.run("251", feedback_seen=set())

    assert code == 0
    assert mock_sleep.call_count == 0
    out = capsys.readouterr().out
    assert f"Codex finished reviewing {HEAD_SHA[:10]}" in out
    assert "Warning" not in out


def test_summary_completed_with_replied_unresolved_threads_warns_and_exits_0(capsys) -> None:
    """block-descent PR #249: findings were replied to but never resolved, so Codex
    never added +1. The watcher must not wait for it."""
    state = _codex_state(has_any_findings=True, has_unresolved_findings=True)
    threads = [
        _thread("Count gold earned against the cap", replied=True),
        _thread("Limit wages inside the queued effect", replied=True, outdated=True),
        _thread("Already resolved", replied=False, resolved=True),
    ]
    with _watch(checks=PASSING, codex_state=state, threads=threads) as mock_sleep:
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 0
    assert mock_sleep.call_count == 0
    out = capsys.readouterr().out
    assert "Warning: 2 review thread(s) are still unresolved" in out
    assert "Count gold earned against the cap" in out
    assert "Limit wages inside the queued effect" in out
    assert "Already resolved" not in out
    assert "P2 Badge" not in out


def test_summary_completed_with_outdated_unreplied_thread_warns_and_exits_0(capsys) -> None:
    state = _codex_state(has_any_findings=True, has_unresolved_findings=True)
    threads = [_thread("Old finding on rewritten code", replied=False, outdated=True)]
    with _watch(checks=PASSING, codex_state=state, threads=threads):
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 0
    assert "[outdated, no reply]" in capsys.readouterr().out


def test_summary_completed_with_unreplied_current_thread_exits_2(capsys) -> None:
    """PR #249 also had a current-code finding nobody replied to; that is real feedback."""
    state = _codex_state(has_any_findings=True, has_unresolved_findings=True)
    threads = [
        _thread("Count gold earned against the cap", replied=True),
        _thread("Apply acquisition gold through every relic-granting path", replied=False),
    ]
    with _watch(checks=PASSING, codex_state=state, threads=threads):
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 2
    out = capsys.readouterr().out
    assert "1 unresolved review thread(s) have no reply from @GregorStocks" in out
    assert "Apply acquisition gold through every relic-granting path" in out
    assert "crates/core/src/campaign.rs:1198" in out
    assert "Count gold earned against the cap" not in out


def test_summary_for_older_commit_ignores_plus_one(capsys) -> None:
    """A completed summary for an earlier head means Codex has not reviewed this head yet,
    even if a +1 is present."""
    state = _codex_state(commit_sha=OLD_SHA)
    with _watch(
        checks=PASSING,
        codex_state=state,
        reactions=[_plus_one()],
        lifecycle=["open", "open", "merged"],
    ) as mock_sleep:
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 0  # exits on "merged", not on the +1
    assert mock_sleep.call_count == 1
    assert "waiting for codex" in capsys.readouterr().out


def test_summary_running_for_head_keeps_waiting(capsys) -> None:
    state = _codex_state(status="running", has_any_findings=None, has_unresolved_findings=None)
    with _watch(checks=PASSING, codex_state=state, lifecycle=["open", "open", "merged"]) as mock_sleep:
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 0
    assert mock_sleep.call_count == 1
    assert f"codex reviewing {HEAD_SHA[:10]}" in capsys.readouterr().out


def test_summary_running_security_review_keeps_waiting() -> None:
    security = {"commit_sha": HEAD_SHA, "has_any_findings": None, "has_unresolved_findings": None, "status": "running"}
    state = _codex_state(security=security)
    assert issue_watch_pr.classify_codex_review(state, HEAD_SHA) == "reviewing"
    with _watch(checks=PASSING, codex_state=state, lifecycle=["open", "open", "merged"]) as mock_sleep:
        issue_watch_pr.run("249", feedback_seen=set())
    assert mock_sleep.call_count == 1


def test_fresh_summary_waits_for_comment_grace() -> None:
    """Inline comments from the same review may land just after the summary update."""
    state = _codex_state(seconds_old=1)
    with _watch(checks=PASSING, codex_state=state, lifecycle=["open", "open", "merged"]) as mock_sleep:
        issue_watch_pr.run("249", feedback_seen=set())
    assert mock_sleep.call_count == 1


def test_summary_completed_with_young_new_feedback_does_not_exit_clean() -> None:
    young = [_feedback("[INLINE] @codex on a.py:1: bug", seconds_old=2, id="inline:9")]
    with _watch(
        checks=PASSING,
        codex_state=_codex_state(),
        feedback=young,
        lifecycle=["open", "open", "merged"],
    ) as mock_sleep:
        issue_watch_pr.run("249", feedback_seen=set())
    assert mock_sleep.call_count == 1


def test_summary_completed_but_ci_pending_keeps_waiting() -> None:
    pending = [{"name": "build", "bucket": "pending", "link": "l"}]
    with _watch(checks=pending, codex_state=_codex_state(), lifecycle=["open", "open", "merged"]) as mock_sleep:
        issue_watch_pr.run("249", feedback_seen=set())
    assert mock_sleep.call_count == 1


def test_plus_one_from_before_head_commit_is_not_approval(capsys) -> None:
    """Without a summary comment, a +1 left on an earlier head must not approve a new push."""
    stale = _plus_one(created_at="2026-11-02T00:10:00Z")  # before HEAD_COMMITTED_AT
    with _watch(checks=PASSING, reactions=[stale], lifecycle=["open", "open", "merged"]) as mock_sleep:
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 0
    assert mock_sleep.call_count == 1
    assert "Codex approved" not in capsys.readouterr().out


def test_plus_one_after_head_commit_is_approval(capsys) -> None:
    with _watch(checks=PASSING, reactions=[_plus_one()]) as mock_sleep:
        code = issue_watch_pr.run("251", feedback_seen=set())

    assert code == 0
    assert mock_sleep.call_count == 0
    assert "Codex approved (thumbs up)" in capsys.readouterr().out


def test_ended_summary_status_exits_0_when_ci_passes(capsys) -> None:
    state = _codex_state(status="failed", has_any_findings=None, has_unresolved_findings=None)
    assert issue_watch_pr.classify_codex_review(state, HEAD_SHA) == "ended"
    with _watch(checks=PASSING, codex_state=state):
        code = issue_watch_pr.run("249", feedback_seen=set())
    assert code == 0
    assert "ended with status 'failed'" in capsys.readouterr().out


def test_classify_codex_review_tolerates_missing_and_unknown_fields() -> None:
    assert issue_watch_pr.classify_codex_review(None, HEAD_SHA) == "absent"
    assert issue_watch_pr.classify_codex_review({}, HEAD_SHA) == "stale"
    assert issue_watch_pr.classify_codex_review({"code_review": None}, HEAD_SHA) == "stale"
    state = {"code_review": {"commit_sha": HEAD_SHA, "status": "queued", "new_field": 1}}
    assert issue_watch_pr.classify_codex_review(state, HEAD_SHA) == "reviewing"
    state = {"code_review": {"commit_sha": HEAD_SHA, "status": "completed"}, "extra": {}}
    assert issue_watch_pr.classify_codex_review(state, HEAD_SHA) == "completed"


def test_get_codex_review_state_reads_latest_codex_summary() -> None:
    comments = [
        {"login": PR_AUTHOR, "body": _summary_body(commit_sha="f" * 40), "created_at": "2026-11-02T01:00:00Z", "updated_at": "2026-11-02T01:00:00Z"},
        {"login": CODEX_LOGIN, "body": "@codex review", "created_at": "2026-11-01T22:00:00Z", "updated_at": "2026-11-01T22:00:00Z"},
        {"login": CODEX_LOGIN, "body": _summary_body(commit_sha=OLD_SHA), "created_at": "2026-11-01T22:59:57Z", "updated_at": "2026-11-01T23:30:00Z"},
        {"login": CODEX_LOGIN, "body": _summary_body(has_unresolved_findings=True), "created_at": "2026-11-01T22:59:57Z", "updated_at": "2026-11-02T00:17:52Z"},
    ]
    stdout = "\n".join(json.dumps(c) for c in comments) + "\n"
    with patch.object(issue_watch_pr, "run_gh", return_value=_gh_result(stdout)) as mock_gh:
        state = _real_get_codex_review_state("249", "GregorStocks/block-descent")

    assert mock_gh.call_args.args[:3] == ("api", "--paginate", "repos/GregorStocks/block-descent/issues/249/comments")
    assert state is not None
    assert state["code_review"]["commit_sha"] == HEAD_SHA
    assert state["code_review"]["has_unresolved_findings"] is True
    assert state["updated_at"] == datetime(2026, 11, 2, 0, 17, 52, tzinfo=timezone.utc)


def test_get_codex_review_state_returns_none_without_summary() -> None:
    stdout = json.dumps({"login": CODEX_LOGIN, "body": "hi", "created_at": "2026-11-01T22:00:00Z", "updated_at": None}) + "\n"
    with patch.object(issue_watch_pr, "run_gh", return_value=_gh_result(stdout)):
        assert _real_get_codex_review_state("1", "o/r") is None


def test_parse_codex_review_state_ignores_malformed_marker() -> None:
    assert issue_watch_pr.parse_codex_review_state("<!-- codex-review-state:v1 {not json} -->") is None
    assert issue_watch_pr.parse_codex_review_state("no marker") is None


def test_get_pr_head_reads_head_commit_date() -> None:
    payload = {
        "headRefOid": HEAD_SHA,
        "author": {"login": PR_AUTHOR},
        "commits": [
            {"oid": OLD_SHA, "committedDate": "2026-11-02T00:10:23Z"},
            {"oid": HEAD_SHA, "committedDate": "2026-11-02T00:12:49Z"},
        ],
    }
    with patch.object(issue_watch_pr, "run_gh", return_value=_gh_result(json.dumps(payload))):
        head = _real_get_pr_head("249")
    assert head == {"sha": HEAD_SHA, "committed_at": HEAD_COMMITTED_AT, "author": PR_AUTHOR}


def test_review_feedback_excludes_codex_summary_comment() -> None:
    pr_view = {
        "author": {"login": PR_AUTHOR},
        "reviews": [],
        "comments": [
            {"id": "IC_1", "author": {"login": "chatgpt-codex-connector"}, "body": _summary_body(), "createdAt": "2026-11-01T22:59:57Z"},
            {"id": "IC_2", "author": {"login": "chatgpt-codex-connector"}, "body": "Real comment", "createdAt": "2026-11-01T23:00:00Z"},
        ],
    }
    with patch.object(
        issue_watch_pr,
        "run_gh",
        side_effect=[_gh_result(json.dumps(pr_view)), _gh_result("")],
    ):
        feedback = issue_watch_pr.get_review_feedback("249", "o/r")
    assert [f["id"] for f in feedback] == ["comment:IC_2"]


def test_thread_fetch_failure_keeps_polling_instead_of_exiting_clean(capsys) -> None:
    state = _codex_state(has_any_findings=True, has_unresolved_findings=True)
    with (
        _watch(checks=PASSING, codex_state=state, lifecycle=["open", "open", "merged"]) as mock_sleep,
        patch.object(issue_watch_pr, "run_gh", return_value=_gh_result("", returncode=1, stderr="boom")),
    ):
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 0  # exits on "merged" after retrying
    assert mock_sleep.call_count == 1
    out = capsys.readouterr().out
    assert "could not fetch review threads" in out
    assert "No review feedback" not in out


def test_unresolved_security_findings_are_checked(capsys) -> None:
    security = {"commit_sha": HEAD_SHA, "has_any_findings": True, "has_unresolved_findings": True, "status": "completed"}
    state = _codex_state(security=security)
    threads = [_thread("Validate untrusted input", replied=False)]
    with _watch(checks=PASSING, codex_state=state, threads=threads):
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 2
    assert "Validate untrusted input" in capsys.readouterr().out


def test_review_threads_follow_pagination() -> None:
    page1 = {
        "data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": True, "endCursor": "CUR1"},
            "nodes": [_thread("First page finding", replied=True)],
        }}}}
    }
    page2 = {
        "data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [_thread("Second page finding", replied=False)],
        }}}}
    }
    with patch.object(
        issue_watch_pr,
        "run_gh",
        side_effect=[_gh_result(json.dumps(page1)), _gh_result(json.dumps(page2))],
    ) as mock_gh:
        threads = issue_watch_pr.get_unresolved_review_threads("249", "o/r", PR_AUTHOR)

    assert threads is not None
    assert [t["addressed"] for t in threads] == [True, False]
    assert "cursor=CUR1" in mock_gh.call_args_list[1].args
    assert "cursor=CUR1" not in mock_gh.call_args_list[0].args


def test_failed_code_review_with_running_security_review_is_reviewing() -> None:
    security = {"commit_sha": HEAD_SHA, "has_any_findings": None, "has_unresolved_findings": None, "status": "running"}
    state = _codex_state(status="failed", security=security)
    assert issue_watch_pr.classify_codex_review(state, HEAD_SHA) == "reviewing"


def test_completed_summary_prevents_no_eyes_timeout_bypassing_thread_gate(capsys) -> None:
    """CI ran past the no-eyes timeout; the completed summary must still route through
    the unresolved-thread check instead of "No codex review"."""
    pending = [{"name": "build", "bucket": "pending", "link": "l"}]
    state = _codex_state(has_any_findings=True, has_unresolved_findings=True)
    threads = [_thread("Apply acquisition gold through every relic-granting path", replied=False)]
    with (
        _watch(checks=PASSING, codex_state=state, threads=threads),
        patch.object(issue_watch_pr, "get_checks", side_effect=[pending, PASSING]),
        patch.object(
            issue_watch_pr.time,
            "monotonic",
            side_effect=[0.0, issue_watch_pr.NO_EYES_TIMEOUT + 1, issue_watch_pr.NO_EYES_TIMEOUT + 31],
        ),
    ):
        code = issue_watch_pr.run("249", feedback_seen=set())

    assert code == 2
    out = capsys.readouterr().out
    assert "No codex review" not in out
    assert "Apply acquisition gold" in out
