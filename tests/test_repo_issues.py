"""Validation for this repository's issue queue."""

from pathlib import Path

from agent_issues.cli.issue_lint import lint_issues


def test_repo_issue_files_are_valid() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    errors = lint_issues(repo_root)

    assert errors == []
