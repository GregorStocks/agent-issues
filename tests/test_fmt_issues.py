"""Tests for agent_issues.cli.issue_fmt."""

import json
from pathlib import Path

from agent_issues.cli import issue_fmt
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


def test_fmt_rewrites_long_description(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["description"] = "word " * 200
    path = issues_dir / "p2-long-desc.json5"
    # Write with plain json (no wrapping).
    path.write_text(json.dumps(issue, indent=2))
    assert issue_fmt.fmt_issue(path) is True
    # After formatting, no line should exceed 80 chars.
    for line in path.read_text().split("\n"):
        assert len(line) <= 80, f"Line too long ({len(line)}): {line!r}"


def test_fmt_is_idempotent(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue = _make_valid_issue()
    issue["description"] = "word " * 200
    path = issues_dir / "p2-idempotent.json5"
    path.write_text(dumps_json5(issue) + "\n")
    assert issue_fmt.fmt_issue(path) is False  # already formatted


def test_fmt_orders_fields(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    # Write with fields in reverse order.
    issue = _make_valid_issue()
    reversed_issue = dict(reversed(list(issue.items())))
    path = issues_dir / "p2-order.json5"
    path.write_text(json.dumps(reversed_issue, indent=2))
    assert issue_fmt.fmt_issue(path) is True
    text = path.read_text()
    # title should appear before description.
    assert text.index('"title"') < text.index('"description"')
