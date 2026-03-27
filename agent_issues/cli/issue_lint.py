"""Validate issue JSON5 files in an issues directory."""

import re
import sys
from pathlib import Path

import pyjson5

from agent_issues.issue_files import iter_issue_files, load_issue

REQUIRED_FIELDS = {
    "title",
    "description",
    "status",
    "priority",
    "type",
    "labels",
    "created_at",
    "updated_at",
}

OPTIONAL_FIELDS = {"blocked"}

KNOWN_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
FILENAME_RE = re.compile(r"^(p[1-4]|blocked)-[a-z0-9][a-z0-9-]*$")
MAX_LINE_LENGTH = 120


def _expected_filename_prefix(issue: dict) -> str:
    return "blocked" if issue.get("blocked") else f"p{issue['priority']}"


def lint_issues(project_root: Path) -> list[str]:
    issues_dir = project_root / "issues"
    if not issues_dir.exists():
        return []

    errors = [
        f"{legacy_issue_file.name}: legacy issue file extension; rename to .json5"
        for legacy_issue_file in sorted(issues_dir.glob("*.json"))
    ]

    for issue_file in iter_issue_files(issues_dir):
        try:
            issue = load_issue(issue_file)
        except pyjson5.Json5DecoderException as exc:
            errors.append(f"{issue_file.name}: invalid JSON5 - {exc}")
            continue

        if "id" in issue:
            errors.append(f"{issue_file.name}: has 'id' field (filename serves as id)")

        missing = REQUIRED_FIELDS - set(issue.keys())
        if missing:
            errors.append(f"{issue_file.name}: missing fields: {', '.join(sorted(missing))}")
            continue

        unknown = set(issue.keys()) - KNOWN_FIELDS
        if unknown:
            errors.append(f"{issue_file.name}: unknown fields: {', '.join(sorted(unknown))}")

        if not FILENAME_RE.fullmatch(issue_file.stem):
            errors.append(
                f"{issue_file.name}: filename must start with p1-/p2-/p3-/p4-/blocked- and use kebab-case"
            )
        else:
            expected_prefix = _expected_filename_prefix(issue)
            actual_prefix = issue_file.stem.split("-", 1)[0]
            if actual_prefix != expected_prefix:
                errors.append(
                    f"{issue_file.name}: filename prefix must be '{expected_prefix}-' for this issue"
                )

        if issue["status"] != "open":
            errors.append(
                f"{issue_file.name}: status is '{issue['status']}' (delete resolved issues)"
            )

        if not isinstance(issue["priority"], int) or not 1 <= issue["priority"] <= 4:
            errors.append(
                f"{issue_file.name}: priority must be int 1-4, got {issue['priority']}"
            )

        if not isinstance(issue["labels"], list):
            errors.append(f"{issue_file.name}: labels must be an array")

        if "blocked" in issue and not isinstance(issue["blocked"], (bool, str)):
            errors.append(f"{issue_file.name}: blocked must be a boolean or string")

        raw = issue_file.read_text()
        for line_no, raw_line in enumerate(raw.split("\n"), 1):
            if len(raw_line) > MAX_LINE_LENGTH:
                errors.append(
                    f"{issue_file.name}:{line_no}: line too long "
                    f"({len(raw_line)} > {MAX_LINE_LENGTH}); run issue-fmt"
                )
                break

    return errors


def main() -> None:
    if len(sys.argv) > 1:
        project_root = Path(sys.argv[1]).resolve()
    else:
        project_root = Path.cwd()
    errors = lint_issues(project_root)

    if errors:
        print("Issue validation errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        sys.exit(1)

    print("Issues: OK")
