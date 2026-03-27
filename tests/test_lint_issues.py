"""Tests for agent_issues.cli.issue_lint."""

from pathlib import Path

from agent_issues.cli import issue_lint
from agent_issues.json5_utils import dumps_json5


def _make_valid_issue() -> dict:
    return {
        "title": "Test issue",
        "description": "A test issue",
        "status": "open",
        "priority": 2,
        "type": "bug",
        "labels": ["test"],
        "created_at": "2026-03-01T12:00:00.000000-08:00",
        "updated_at": "2026-03-01T12:00:00.000000-08:00",
    }


def _write_issue(issues_dir: Path, name: str, data: dict) -> None:
    (issues_dir / f"{name}.json5").write_text(dumps_json5(data))


def test_passes_on_valid_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    _write_issue(issues_dir, "p2-good-issue", _make_valid_issue())
    assert issue_lint.lint_issues(tmp_path) == []


def test_passes_with_optional_fields(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["blocked"] = True
    _write_issue(issues_dir, "blocked-good-issue", issue)
    assert issue_lint.lint_issues(tmp_path) == []


def test_passes_with_string_blocked(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["blocked"] = "Waiting for upstream dependency fix."
    _write_issue(issues_dir, "blocked-good-issue", issue)
    assert issue_lint.lint_issues(tmp_path) == []


def test_passes_on_json5_syntax(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p2-json5-issue.json5").write_text(
        """{
  title: "Test issue",
  description: "A test issue",
  status: "open",
  priority: 2,
  type: "bug",
  labels: ["test"],
  created_at: "2026-03-01T12:00:00.000000-08:00",
  updated_at: "2026-03-01T12:00:00.000000-08:00",
}
"""
    )
    assert issue_lint.lint_issues(tmp_path) == []


def test_catches_missing_field(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    del issue["title"]
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = issue_lint.lint_issues(tmp_path)
    assert len(errors) == 1
    assert "missing fields" in errors[0]
    assert "title" in errors[0]


def test_catches_unknown_field(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["autograbbable"] = False
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = issue_lint.lint_issues(tmp_path)
    assert len(errors) == 1
    assert "unknown fields" in errors[0]
    assert "autograbbable" in errors[0]


def test_catches_id_field(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["id"] = "should-not-exist"
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = issue_lint.lint_issues(tmp_path)
    assert any("has 'id' field" in e for e in errors)


def test_catches_priority_prefix_mismatch(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    _write_issue(issues_dir, "p3-bad-issue", _make_valid_issue())
    errors = issue_lint.lint_issues(tmp_path)
    assert any("filename prefix must be 'p2-'" in e for e in errors)


def test_catches_blocked_prefix_mismatch(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["blocked"] = True
    _write_issue(issues_dir, "p2-bad-issue", issue)
    errors = issue_lint.lint_issues(tmp_path)
    assert any("filename prefix must be 'blocked-'" in e for e in errors)


def test_catches_missing_required_prefix(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    _write_issue(issues_dir, "bad-issue", _make_valid_issue())
    errors = issue_lint.lint_issues(tmp_path)
    assert any("filename must start with p1-/p2-/p3-/p4-/blocked-" in e for e in errors)


def test_catches_legacy_json_extension(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p2-legacy.json").write_text("{}")
    errors = issue_lint.lint_issues(tmp_path)
    assert errors == ["p2-legacy.json: legacy issue file extension; rename to .json5"]


def test_catches_long_lines(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["description"] = "word " * 200  # ~1000 chars on one line
    # Write raw JSON to avoid dumps_json5 wrapping it for us.
    import json

    (issues_dir / "p2-long-lines.json5").write_text(json.dumps(issue, indent=2))
    errors = issue_lint.lint_issues(tmp_path)
    assert any("line too long" in e for e in errors)
    assert any("issue-fmt" in e for e in errors)


def test_formatted_issue_passes_line_length(tmp_path: Path) -> None:
    """An issue written through dumps_json5 should never trigger line-length."""
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["description"] = "word " * 200
    _write_issue(issues_dir, "p2-wrapped-issue", issue)
    errors = issue_lint.lint_issues(tmp_path)
    assert not any("line too long" in e for e in errors)
