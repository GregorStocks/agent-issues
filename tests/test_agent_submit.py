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
        ["--title", "T", "--body", "B", "--draft", "--base", "develop", "--force"]
    )
    assert args.draft is True
    assert args.base == "develop"
    assert args.force is True


def test_force_defaults_to_false() -> None:
    args = agent_submit.parse_args(["--title", "T", "--body", "B"])
    assert args.force is False


def test_push_omits_force_by_default() -> None:
    with patch("agent_issues.cli.agent_submit.subprocess.run") as run_mock:
        run_mock.return_value = CompletedProcess(args=[], returncode=0)
        agent_submit._push()
    cmd = run_mock.call_args.args[0]
    assert cmd == ["git", "push", "origin", "HEAD"]


def test_push_uses_force_with_lease_when_requested() -> None:
    with patch("agent_issues.cli.agent_submit.subprocess.run") as run_mock:
        run_mock.return_value = CompletedProcess(args=[], returncode=0)
        agent_submit._push(force=True)
    cmd = run_mock.call_args.args[0]
    assert cmd == ["git", "push", "--force-with-lease", "origin", "HEAD"]


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


def test_upsert_pr_creates_when_none_exists() -> None:
    results = [
        _result(stdout="[]"),  # gh pr list
        _result(stdout="https://github.com/o/r/pull/7\n"),  # gh pr create
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        pr_num = agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=False
        )
    assert pr_num == "7"
    calls = [call.args[0] for call in run_mock.call_args_list]
    assert calls[0][:4] == ["gh", "pr", "list", "--head"]
    assert calls[1][:3] == ["gh", "pr", "create"]
    assert "--draft" not in calls[1]


def test_upsert_pr_creates_draft_when_flag_set() -> None:
    results = [
        _result(stdout="[]"),
        _result(stdout="https://github.com/o/r/pull/8\n"),
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=True
        )
    create_call = run_mock.call_args_list[1].args[0]
    assert "--draft" in create_call


def test_upsert_pr_edits_when_one_exists() -> None:
    import json as _json
    results = [
        _result(stdout=_json.dumps([{"number": 5}])),
        _result(stdout=""),  # gh pr edit
        _result(stdout="https://github.com/o/r/pull/5\n"),  # gh pr view for URL
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        pr_num = agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=True
        )
    assert pr_num == "5"
    edit_call = run_mock.call_args_list[1].args[0]
    assert edit_call[:3] == ["gh", "pr", "edit"]
    assert "5" in edit_call
    # --draft must NOT be passed on edit
    assert "--draft" not in edit_call


def test_upsert_pr_aborts_when_multiple_open_prs(capsys) -> None:
    import json as _json
    results = [
        _result(stdout=_json.dumps([{"number": 5}, {"number": 6}])),
    ]
    with patch.object(agent_submit, "_run", side_effect=results), pytest.raises(SystemExit) as exc:
        agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body="B", draft=False
        )
    assert exc.value.code == 10
    assert "more than one" in capsys.readouterr().out.lower()


def test_next_step_footer_for_exit_1(capsys) -> None:
    agent_submit._print_next_step(1)
    out = capsys.readouterr().out
    assert "CI failed" in out
    assert "agent-submit" in out


def test_next_step_footer_for_exit_2(capsys) -> None:
    agent_submit._print_next_step(2)
    assert "Review feedback" in capsys.readouterr().out


def test_next_step_footer_for_exit_4(capsys) -> None:
    agent_submit._print_next_step(4)
    out = capsys.readouterr().out
    assert "timed out" in out.lower()
    assert "wait for the user" in out.lower()


def test_next_step_footer_silent_on_exit_0(capsys) -> None:
    agent_submit._print_next_step(0)
    assert capsys.readouterr().out == ""


def test_main_runs_full_flow_and_relays_watcher_exit() -> None:
    from agent_issues.cli import issue_watch_pr
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=0) as push_mock,
        patch.object(agent_submit, "upsert_pr", return_value="42"),
        patch.object(issue_watch_pr, "run", return_value=2) as watcher_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 2
    watcher_mock.assert_called_once_with(pr="42")
    push_mock.assert_called_once_with(force=False)


def test_main_passes_force_flag_to_push() -> None:
    from agent_issues.cli import issue_watch_pr
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B", "--force"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=0) as push_mock,
        patch.object(agent_submit, "upsert_pr", return_value="42"),
        patch.object(issue_watch_pr, "run", return_value=0),
        pytest.raises(SystemExit),
    ):
        agent_submit.main()
    push_mock.assert_called_once_with(force=True)


def test_main_exits_early_on_preflight_failure() -> None:
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=10),
        patch.object(agent_submit, "_push") as push_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 10
    push_mock.assert_not_called()


def test_main_exits_on_push_failure_without_upserting() -> None:
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=128),
        patch.object(agent_submit, "upsert_pr") as upsert_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 128
    upsert_mock.assert_not_called()
