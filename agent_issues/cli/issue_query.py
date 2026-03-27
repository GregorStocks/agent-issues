"""Query issues from the issues directory."""

import argparse
from pathlib import Path

from agent_issues.issue_files import iter_issue_files, load_issue

ISSUES_DIR = Path("issues")


def main() -> None:
    parser = argparse.ArgumentParser(description="Query issues")
    parser.add_argument("--label", type=str, help="Filter by label")
    parser.add_argument("--max-priority", type=int, help="Show issues with priority <= N")
    parser.add_argument("--search", type=str, help="Search titles and descriptions")
    args = parser.parse_args()

    assert ISSUES_DIR.is_dir(), f"Issues directory not found: {ISSUES_DIR}"

    issues = []
    for issue_file in iter_issue_files(ISSUES_DIR):
        data = load_issue(issue_file)
        data["_filename"] = issue_file.stem
        issues.append(data)

    if args.label:
        issues = [issue for issue in issues if (labels := issue.get("labels")) and args.label in labels]

    if args.max_priority is not None:
        issues = [issue for issue in issues if issue.get("priority", 999) <= args.max_priority]

    if args.search:
        term = args.search.lower()
        issues = [
            issue
            for issue in issues
            if term in issue["title"].lower()
            or (issue.get("description") and term in issue["description"].lower())
        ]

    issues.sort(key=lambda issue: issue.get("priority", 999))

    for issue in issues:
        print(f"{issue['_filename']}: {issue.get('priority', '?')}\t{issue['title']}")
