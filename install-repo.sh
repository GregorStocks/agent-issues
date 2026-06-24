#!/bin/bash
# Opt a repository into the agent-issues workflow.
#
# Usage:
#   ./install-repo.sh [repo-root]
#
# This first installs the global agent-issues CLI and skills, then adds
# repo-local agent guidance without rewriting existing content.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${1:-$(pwd)}"
AGENTS_FILE="$REPO_ROOT/AGENTS.md"
CLAUDE_FILE="$REPO_ROOT/CLAUDE.md"
AGENT_ISSUES_LINE='Use the `agent-issues` skill for this repository'\''s local issue tracking, branch, and PR workflow.'

if [ ! -d "$REPO_ROOT" ]; then
    echo "Error: repository root does not exist: $REPO_ROOT" >&2
    exit 1
fi

ensure_line() {
    local file="$1"
    local line="$2"

    mkdir -p "$(dirname "$file")"
    touch "$file"

    if grep -Fxq "$line" "$file"; then
        echo "Line already present: $file"
        return
    fi

    if [ -s "$file" ]; then
        printf '\n%s\n' "$line" >> "$file"
    else
        printf '%s\n' "$line" >> "$file"
    fi
    echo "Added line: $file -> $line"
}

"$SCRIPT_DIR/install.sh"

ensure_line "$AGENTS_FILE" "$AGENT_ISSUES_LINE"
ensure_line "$CLAUDE_FILE" "@AGENTS.md"

echo ""
echo "Repository opted into agent-issues:"
echo "  $REPO_ROOT"
