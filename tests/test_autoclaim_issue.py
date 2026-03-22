"""Tests for bin/issue-autoclaim."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_issues.json5_utils import dumps_json5
from agent_issues.local_claims import ClaimRecord

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


def _import_script(name: str):
    path = BIN_DIR / name
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader, origin=str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


issue_autoclaim = _import_script("issue-autoclaim")


def _claim_record(key: str) -> ClaimRecord:
    return ClaimRecord(
        namespace="issues",
        key=key,
        claim_path=Path(f"/tmp/{key}.json"),
        worktree_path=Path("/tmp/wt"),
        worktree_name="wt",
        branch="feature",
        payload={"key": key},
    )


def test_load_issues_skips_blocked_and_sorts_by_priority(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p3-third.json5").write_text(dumps_json5({"title": "Third", "priority": 3}))
    (issues_dir / "blocked-manual.json5").write_text(dumps_json5({"title": "Manual", "priority": 1, "blocked": True}))
    (issues_dir / "p1-first.json5").write_text(
        """{
  title: "First",
  priority: 1,
}
"""
    )

    issue_autoclaim.ISSUES_DIR = issues_dir

    assert issue_autoclaim.load_issues() == [
        ("p1-first", 1, "First"),
        ("p3-third", 3, "Third"),
    ]


def test_claim_specific_uses_local_claim_backend(tmp_path: Path, capsys) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    issue_autoclaim.ISSUES_DIR = issues_dir

    with (
        patch.object(
            issue_autoclaim,
            "claim_exact_keys",
            return_value=[_claim_record("first")],
        ) as mock_claim,
        patch.object(issue_autoclaim, "current_owner_claims", return_value=[]),
    ):
        issue_autoclaim.claim_specific("p1-first")

    mock_claim.assert_called_once()
    assert capsys.readouterr().out == "Claimed: p1-first\n"


def test_main_auto_claims_first_available(tmp_path: Path, capsys) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    issue_autoclaim.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["issue-autoclaim"]),
        patch.object(issue_autoclaim, "merge_default_branch"),
        patch.object(
            issue_autoclaim,
            "_default_branch",
            return_value="main",
        ),
        patch.object(
            issue_autoclaim,
            "current_worktree_context",
            return_value=MagicMock(branch="feature"),
        ),
        patch.object(issue_autoclaim, "current_owner_claims", return_value=[]),
        patch.object(
            issue_autoclaim,
            "claim_first_available_keys",
            return_value=[_claim_record("first")],
        ) as mock_claim,
    ):
        issue_autoclaim.main()

    mock_claim.assert_called_once()
    assert capsys.readouterr().out == "Claimed: p1-first\n"


def test_main_exits_1_when_no_claimable_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue_autoclaim.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["issue-autoclaim"]),
        patch.object(issue_autoclaim, "merge_default_branch"),
        patch.object(
            issue_autoclaim,
            "_default_branch",
            return_value="main",
        ),
        patch.object(
            issue_autoclaim,
            "current_worktree_context",
            return_value=MagicMock(branch="feature"),
        ),
        patch.object(issue_autoclaim, "current_owner_claims", return_value=[]),
        patch.object(issue_autoclaim, "claim_first_available_keys", return_value=[]),
        pytest.raises(SystemExit, match="1"),
    ):
        issue_autoclaim.main()


def test_main_exits_2_when_worktree_already_claims_issue(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    issue_autoclaim.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["issue-autoclaim"]),
        patch.object(issue_autoclaim, "merge_default_branch"),
        patch.object(
            issue_autoclaim,
            "_default_branch",
            return_value="main",
        ),
        patch.object(
            issue_autoclaim,
            "current_worktree_context",
            return_value=MagicMock(branch="feature"),
        ),
        patch.object(
            issue_autoclaim,
            "current_owner_claims",
            return_value=[_claim_record("first")],
        ),
        pytest.raises(SystemExit, match="2"),
    ):
        issue_autoclaim.main()


def test_main_exits_2_on_default_branch(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue_autoclaim.ISSUES_DIR = issues_dir

    with (
        patch.object(sys, "argv", ["issue-autoclaim"]),
        patch.object(issue_autoclaim, "merge_default_branch"),
        patch.object(
            issue_autoclaim,
            "_default_branch",
            return_value="main",
        ),
        patch.object(
            issue_autoclaim,
            "current_worktree_context",
            return_value=MagicMock(branch="main"),
        ),
        pytest.raises(SystemExit, match="2"),
    ):
        issue_autoclaim.main()


def test_claim_specific_exits_2_when_worktree_already_claims_other_issue(
    tmp_path: Path,
) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    (issues_dir / "p2-second.json5").write_text(dumps_json5({"title": "Second", "priority": 2}))
    issue_autoclaim.ISSUES_DIR = issues_dir

    with (
        patch.object(
            issue_autoclaim,
            "current_owner_claims",
            return_value=[_claim_record("first")],
        ),
        pytest.raises(SystemExit, match="2"),
    ):
        issue_autoclaim.claim_specific("p2-second")
