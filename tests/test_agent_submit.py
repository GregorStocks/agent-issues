"""Tests for agent_issues.cli.agent_submit."""

from pathlib import Path
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
    assert args.allow_escaped_backticks is False


def test_parses_optional_flags() -> None:
    args = agent_submit.parse_args(
        [
            "--title",
            "T",
            "--body",
            "B",
            "--draft",
            "--base",
            "develop",
            "--force",
            "--allow-escaped-backticks",
        ]
    )
    assert args.draft is True
    assert args.base == "develop"
    assert args.force is True
    assert args.allow_escaped_backticks is True


def test_force_defaults_to_false() -> None:
    args = agent_submit.parse_args(["--title", "T", "--body", "B"])
    assert args.force is False


def test_validate_pr_body_markdown_rejects_escaped_backticks(capsys) -> None:
    code = agent_submit.validate_pr_body_markdown(r"Use \`agent-submit\`")
    assert code == agent_submit.EXIT_PREFLIGHT
    assert "escaped inline-code" in capsys.readouterr().out


def test_validate_pr_body_markdown_allows_other_escapes() -> None:
    assert agent_submit.validate_pr_body_markdown(r"\*literal\* and regex \.") == 0


def test_validate_pr_body_markdown_allows_literal_escaped_backticks_with_flag() -> None:
    assert (
        agent_submit.validate_pr_body_markdown(
            r"Document literal syntax like \`code\`", allow_escaped_backticks=True
        )
        == 0
    )


def test_load_submit_hooks_returns_empty_when_config_absent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    hooks = agent_submit.load_submit_hooks()

    assert hooks.root == tmp_path
    assert hooks.config_path is None
    assert hooks.prepare == ()
    assert hooks.after_publish == ()


def test_load_submit_hooks_reads_nearest_repo_config(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / ".agent-issues/submit-hooks.json5"
    nested = tmp_path / "a/b"
    config.parent.mkdir(parents=True)
    nested.mkdir(parents=True)
    config.write_text(
        """
        {
          prepare: ["make agent-submit-prepare"],
          after_publish: ["make agent-submit-after-publish"],
        }
        """
    )
    monkeypatch.chdir(nested)

    hooks = agent_submit.load_submit_hooks()

    assert hooks.root == tmp_path
    assert hooks.config_path == config
    assert hooks.prepare == ("make agent-submit-prepare",)
    assert hooks.after_publish == ("make agent-submit-after-publish",)


def test_load_submit_hooks_rejects_unknown_keys(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / ".agent-issues/submit-hooks.json5"
    config.parent.mkdir(parents=True)
    config.write_text("{presubmit: ['make check']}\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="unknown submit hook"):
        agent_submit.load_submit_hooks()


def test_load_submit_hooks_rejects_non_string_commands(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / ".agent-issues/submit-hooks.json5"
    config.parent.mkdir(parents=True)
    config.write_text("{prepare: ['make check', 7]}\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match=r"prepare\[1\]"):
        agent_submit.load_submit_hooks()


def test_run_submit_hooks_passes_env_and_cwd(tmp_path: Path) -> None:
    hooks = agent_submit.SubmitHooks(
        root=tmp_path,
        config_path=tmp_path / ".agent-issues/submit-hooks.json5",
        prepare=("make agent-submit-prepare",),
    )
    with patch("agent_issues.cli.agent_submit.subprocess.run") as run_mock:
        run_mock.return_value = CompletedProcess(args=[], returncode=0)
        code = agent_submit.run_submit_hooks(
            hooks,
            "prepare",
            branch="feature-x",
            base="main",
            sha="abc123",
        )

    assert code == 0
    assert run_mock.call_args.args[0] == "make agent-submit-prepare"
    kwargs = run_mock.call_args.kwargs
    assert kwargs["cwd"] == tmp_path
    assert kwargs["shell"] is True
    assert kwargs["env"]["AGENT_SUBMIT_PHASE"] == "prepare"
    assert kwargs["env"]["AGENT_SUBMIT_REPO_ROOT"] == str(tmp_path)
    assert kwargs["env"]["AGENT_SUBMIT_BRANCH"] == "feature-x"
    assert kwargs["env"]["AGENT_SUBMIT_BASE"] == "main"
    assert kwargs["env"]["AGENT_SUBMIT_SHA"] == "abc123"
    assert "AGENT_SUBMIT_PR_NUMBER" not in kwargs["env"]


def test_run_submit_hooks_passes_pr_number_after_publish(tmp_path: Path) -> None:
    hooks = agent_submit.SubmitHooks(
        root=tmp_path,
        after_publish=("make agent-submit-after-publish",),
    )
    with patch("agent_issues.cli.agent_submit.subprocess.run") as run_mock:
        run_mock.return_value = CompletedProcess(args=[], returncode=0)
        code = agent_submit.run_submit_hooks(
            hooks,
            "after_publish",
            branch="feature-x",
            base="main",
            sha="def456",
            pr_number="42",
        )

    assert code == 0
    assert run_mock.call_args.kwargs["env"]["AGENT_SUBMIT_PR_NUMBER"] == "42"


def test_run_submit_hooks_maps_command_failure_to_preflight_exit(
    tmp_path: Path, capsys
) -> None:
    hooks = agent_submit.SubmitHooks(root=tmp_path, prepare=("make check",))
    with patch("agent_issues.cli.agent_submit.subprocess.run") as run_mock:
        run_mock.return_value = CompletedProcess(args=[], returncode=2)
        code = agent_submit.run_submit_hooks(
            hooks,
            "prepare",
            branch="feature-x",
            base="main",
            sha="abc123",
        )

    assert code == agent_submit.EXIT_PREFLIGHT
    assert "prepare hook failed" in capsys.readouterr().out


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


def test_upsert_pr_creates_with_body_unchanged() -> None:
    results = [
        _result(stdout="[]"),  # gh pr list
        _result(stdout="https://github.com/o/r/pull/7\n"),  # gh pr create
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        agent_submit.upsert_pr(
            branch="feature-x",
            base="main",
            title="T",
            body=r"## Summary" "\n\n" r"- Keep \*literal\* text",
            draft=False,
        )
    create_call = run_mock.call_args_list[1].args[0]
    assert create_call[create_call.index("--body") + 1] == (
        r"## Summary" "\n\n" r"- Keep \*literal\* text"
    )


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


def test_upsert_pr_edits_with_body_unchanged() -> None:
    import json as _json

    results = [
        _result(stdout=_json.dumps([{"number": 5}])),
        _result(stdout=""),  # gh pr edit
        _result(stdout="https://github.com/o/r/pull/5\n"),  # gh pr view for URL
    ]
    with patch.object(agent_submit, "_run", side_effect=results) as run_mock:
        agent_submit.upsert_pr(
            branch="feature-x", base="main", title="T", body=r"Use \*literal\*", draft=True
        )
    edit_call = run_mock.call_args_list[1].args[0]
    assert edit_call[edit_call.index("--body") + 1] == r"Use \*literal\*"


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
    assert "consult the user" in out.lower()
    assert "feedback that arrived while stopped" in out
    assert "re-run agent-submit" in out.lower()
    assert "manual PR watching" in out


def test_next_step_footer_silent_on_exit_0(capsys) -> None:
    agent_submit._print_next_step(0)
    assert capsys.readouterr().out == ""


def test_main_runs_full_flow_and_relays_watcher_exit() -> None:
    from agent_issues.cli import issue_watch_pr
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "load_submit_hooks", return_value=agent_submit.SubmitHooks(root=Path.cwd())),
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


def test_main_exits_early_on_invalid_markdown_body(capsys) -> None:
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", r"Use \`code\`"]),
        patch.object(agent_submit, "preflight") as preflight_mock,
        patch.object(agent_submit, "_push") as push_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == agent_submit.EXIT_PREFLIGHT
    assert "escaped inline-code" in capsys.readouterr().out
    preflight_mock.assert_not_called()
    push_mock.assert_not_called()


def test_main_allows_escaped_backticks_with_flag() -> None:
    from agent_issues.cli import issue_watch_pr

    with (
        patch.object(
            sys,
            "argv",
            [
                "agent-submit",
                "--title",
                "T",
                "--body",
                r"Use \`code\`",
                "--allow-escaped-backticks",
            ],
        ),
        patch.object(agent_submit, "preflight", return_value=0) as preflight_mock,
        patch.object(agent_submit, "load_submit_hooks", return_value=agent_submit.SubmitHooks(root=Path.cwd())),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=0),
        patch.object(agent_submit, "upsert_pr", return_value="42"),
        patch.object(issue_watch_pr, "run", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 0
    preflight_mock.assert_called_once()


def test_main_passes_force_flag_to_push() -> None:
    from agent_issues.cli import issue_watch_pr
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B", "--force"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "load_submit_hooks", return_value=agent_submit.SubmitHooks(root=Path.cwd())),
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
        patch.object(agent_submit, "load_submit_hooks", return_value=agent_submit.SubmitHooks(root=Path.cwd())),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_push", return_value=128),
        patch.object(agent_submit, "upsert_pr") as upsert_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()
    assert exc.value.code == 128
    upsert_mock.assert_not_called()


def test_main_runs_submit_hooks_around_publish() -> None:
    from agent_issues.cli import issue_watch_pr

    hooks = agent_submit.SubmitHooks(
        root=Path.cwd(),
        prepare=("make agent-submit-prepare",),
        after_publish=("make agent-submit-after-publish",),
    )
    events: list[tuple[str, str | None, str | None, str | None]] = []

    def run_hooks(
        _hooks: agent_submit.SubmitHooks,
        phase: str,
        *,
        branch: str,
        base: str,
        sha: str,
        pr_number: str | None = None,
    ) -> int:
        events.append((phase, branch, sha, pr_number))
        assert _hooks == hooks
        assert base == "main"
        return 0

    def push(*, force: bool = False) -> int:
        assert force is False
        events.append(("push", None, None, None))
        return 0

    def upsert_pr(**_kwargs: object) -> str:
        events.append(("upsert", None, None, None))
        return "42"

    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "load_submit_hooks", return_value=hooks),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_head_sha", side_effect=["before", "after", "after"]),
        patch.object(agent_submit, "run_submit_hooks", side_effect=run_hooks),
        patch.object(agent_submit, "_ensure_branch_unchanged", return_value=0),
        patch.object(agent_submit, "_ensure_head_unchanged", return_value=0),
        patch.object(agent_submit, "_ensure_clean_worktree", return_value=0),
        patch.object(agent_submit, "_push", side_effect=push),
        patch.object(agent_submit, "upsert_pr", side_effect=upsert_pr),
        patch.object(issue_watch_pr, "run", return_value=0),
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()

    assert exc.value.code == 0
    assert events == [
        ("prepare", "feature-x", "before", None),
        ("push", None, None, None),
        ("upsert", None, None, None),
        ("after_publish", "feature-x", "after", "42"),
    ]


def test_main_exits_when_prepare_hook_fails_before_push() -> None:
    hooks = agent_submit.SubmitHooks(root=Path.cwd(), prepare=("make check",))
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "load_submit_hooks", return_value=hooks),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_head_sha", return_value="abc123"),
        patch.object(agent_submit, "run_submit_hooks", return_value=agent_submit.EXIT_PREFLIGHT),
        patch.object(agent_submit, "_push") as push_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()

    assert exc.value.code == agent_submit.EXIT_PREFLIGHT
    push_mock.assert_not_called()


def test_main_exits_when_prepare_hook_leaves_dirty_tree() -> None:
    hooks = agent_submit.SubmitHooks(root=Path.cwd(), prepare=("make check",))
    with (
        patch.object(sys, "argv", ["agent-submit", "--title", "T", "--body", "B"]),
        patch.object(agent_submit, "preflight", return_value=0),
        patch.object(agent_submit, "load_submit_hooks", return_value=hooks),
        patch.object(agent_submit, "_current_branch", return_value="feature-x"),
        patch.object(agent_submit, "_default_branch", return_value="main"),
        patch.object(agent_submit, "_head_sha", return_value="abc123"),
        patch.object(agent_submit, "run_submit_hooks", return_value=0),
        patch.object(agent_submit, "_ensure_branch_unchanged", return_value=0),
        patch.object(agent_submit, "_ensure_clean_worktree", return_value=agent_submit.EXIT_PREFLIGHT),
        patch.object(agent_submit, "_push") as push_mock,
        pytest.raises(SystemExit) as exc,
    ):
        agent_submit.main()

    assert exc.value.code == agent_submit.EXIT_PREFLIGHT
    push_mock.assert_not_called()
