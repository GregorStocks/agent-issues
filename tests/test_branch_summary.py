"""Tests for agent_issues.cli.branch_summary."""

from subprocess import CompletedProcess
from unittest.mock import patch

from agent_issues.cli import branch_summary


def _result(stdout: str = "", returncode: int = 0, stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_prints_commits_and_diff_stat(capsys) -> None:
    results = [
        _result(),                                           # git fetch
        _result(stdout="abc123 first\ndef456 second\n"),     # git log --oneline
        _result(stdout=" file.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n"),  # git diff --stat
    ]
    with (
        patch.object(branch_summary, "_run", side_effect=results),
        patch.object(branch_summary, "default_branch", return_value="main"),
    ):
        code = branch_summary.summarize()
    assert code == 0
    out = capsys.readouterr().out
    assert "=== commits ahead of origin/main ===" in out
    assert "abc123 first" in out
    assert "=== diff stat vs origin/main ===" in out
    assert "1 file changed" in out


def test_fails_when_fetch_fails(capsys) -> None:
    results = [_result(returncode=1, stderr="network down")]
    with (
        patch.object(branch_summary, "_run", side_effect=results),
        patch.object(branch_summary, "default_branch", return_value="main"),
    ):
        code = branch_summary.summarize()
    assert code == 1
    assert "network down" in capsys.readouterr().out
