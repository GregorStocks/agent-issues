"""Reformat issue JSON5 files for consistent style and line width."""

import sys
from pathlib import Path

from agent_issues.issue_files import iter_issue_files, load_issue
from agent_issues.json5_utils import dumps_json5

FIELD_ORDER = [
    "title",
    "description",
    "status",
    "priority",
    "type",
    "labels",
    "blocked",
    "created_at",
    "updated_at",
]


def format_issue_text(issue: dict) -> str:
    """Return the canonical on-disk representation of an issue dict."""
    ordered: dict = {}
    for field in FIELD_ORDER:
        if field in issue:
            ordered[field] = issue[field]
    for field in issue:
        if field not in ordered:
            ordered[field] = issue[field]
    return dumps_json5(ordered) + "\n"


def fmt_issue(path: Path) -> bool:
    """Reformat a single issue file. Returns True if the file changed."""
    formatted = format_issue_text(load_issue(path))
    if formatted == path.read_text():
        return False
    path.write_text(formatted)
    return True


def main() -> None:
    if len(sys.argv) > 1:
        project_root = Path(sys.argv[1]).resolve()
    else:
        project_root = Path.cwd()

    issues_dir = project_root / "issues"
    if not issues_dir.exists():
        print("No issues/ directory found")
        return

    changed = 0
    for issue_file in iter_issue_files(issues_dir):
        if fmt_issue(issue_file):
            changed += 1
            print(f"Formatted: {issue_file.name}")

    if changed:
        print(f"\n{changed} file(s) reformatted")
    else:
        print("All issues already formatted")
