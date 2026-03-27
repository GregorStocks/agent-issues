"""Abandon the currently claimed issue for this worktree."""

import sys
from pathlib import Path

from agent_issues.local_claims import release_current_owner_claims, resolve_issue_stem_for_key

ISSUES_DIR = Path("issues")
ISSUE_NAMESPACE = "issues"


def main() -> None:
    released = release_current_owner_claims(ISSUE_NAMESPACE)
    if not released:
        print("No active issue claim for this worktree.", file=sys.stderr)
        sys.exit(1)

    for record in released:
        stem = resolve_issue_stem_for_key(ISSUES_DIR, record.key) or record.key
        print(f"Abandoned: {stem}")
