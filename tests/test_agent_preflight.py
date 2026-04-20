"""Tests for agent_issues.cli.agent_preflight."""

from subprocess import CompletedProcess
from unittest.mock import patch

from agent_issues.cli import agent_preflight


def _result(stdout: str = "", returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_runs(results: list[CompletedProcess[str]]):
    # agent-preflight calls common.default_branch() directly, not via _run,
    # so stub both seams to keep tests hermetic.
    return (
        patch.object(agent_preflight, "_run", side_effect=results),
        patch.object(agent_preflight, "default_branch", return_value="main"),
    )


def _apply(results):
    run_patch, branch_patch = _patch_runs(results)
    return run_patch, branch_patch


def test_passes_on_clean_branch_at_origin_default(capsys) -> None:
    results = [
        _result(),                                           # git fetch origin
        _result(stdout="feature-x\n"),                       # git branch --show-current
        _result(),                                           # git status --porcelain
        _result(stdout="abc123\n"),                          # git rev-parse HEAD
        _result(stdout="abc123\n"),                          # git rev-parse origin/main
        _result(stdout="[]"),                                # gh pr list
    ]
    run_patch, branch_patch = _apply(results)
    with run_patch, branch_patch:
        code = agent_preflight.preflight()
    assert code == 0
    out = capsys.readouterr().out
    assert "OK:" in out


def test_fails_on_dirty_tree(capsys) -> None:
    results = [
        _result(),
        _result(stdout="feature-x\n"),
        _result(stdout=" M file.py\n"),
    ]
    run_patch, branch_patch = _apply(results)
    with run_patch, branch_patch:
        code = agent_preflight.preflight()
    assert code == 1
    assert "uncommitted" in capsys.readouterr().out.lower()


def test_fails_when_head_does_not_match_origin_default(capsys) -> None:
    results = [
        _result(),
        _result(stdout="feature-x\n"),
        _result(),
        _result(stdout="deadbeef\n"),
        _result(stdout="cafef00d\n"),
        _result(stdout="deadbeef feat\n"),   # git log (informational)
    ]
    run_patch, branch_patch = _apply(results)
    with run_patch, branch_patch:
        code = agent_preflight.preflight()
    assert code == 1
    out = capsys.readouterr().out
    assert "does not match origin/main" in out


def test_fails_when_branch_has_open_pr(capsys) -> None:
    results = [
        _result(),
        _result(stdout="feature-x\n"),
        _result(),
        _result(stdout="abc\n"),
        _result(stdout="abc\n"),
        _result(stdout='[{"number":42,"title":"wip","url":"https://x/42"}]\n'),
    ]
    run_patch, branch_patch = _apply(results)
    with run_patch, branch_patch:
        code = agent_preflight.preflight()
    assert code == 1
    assert "open PR" in capsys.readouterr().out
