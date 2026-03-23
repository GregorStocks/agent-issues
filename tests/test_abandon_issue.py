"""Tests for bin/issue-abandon."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

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


issue_abandon = _import_script("issue-abandon")


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


def test_abandon_releases_claim(tmp_path: Path, capsys) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    (issues_dir / "p1-first.json5").write_text(dumps_json5({"title": "First", "priority": 1}))
    issue_abandon.ISSUES_DIR = issues_dir

    with patch.object(
        issue_abandon,
        "release_current_owner_claims",
        return_value=[_claim_record("first")],
    ) as mock_release:
        issue_abandon.main()

    mock_release.assert_called_once_with("issues")
    assert capsys.readouterr().out == "Abandoned: p1-first\n"


def test_abandon_exits_1_when_no_claim(tmp_path: Path) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue_abandon.ISSUES_DIR = issues_dir

    with (
        patch.object(issue_abandon, "release_current_owner_claims", return_value=[]),
        pytest.raises(SystemExit, match="1"),
    ):
        issue_abandon.main()


def test_abandon_falls_back_to_key_when_issue_file_missing(tmp_path: Path, capsys) -> None:
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue_abandon.ISSUES_DIR = issues_dir

    with patch.object(
        issue_abandon,
        "release_current_owner_claims",
        return_value=[_claim_record("gone")],
    ):
        issue_abandon.main()

    assert capsys.readouterr().out == "Abandoned: gone\n"
