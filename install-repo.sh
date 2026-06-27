#!/bin/bash
# Opt a repository into the agent-issues workflow.
#
# Usage:
#   ./install-repo.sh [repo-root]
#
# This first installs the global agent-issues CLI and skills, then delegates
# repo-local scaffolding to `agent-issues init`.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${1:-$(pwd)}"

if [ ! -d "$REPO_ROOT" ]; then
    echo "Error: repository root does not exist: $REPO_ROOT" >&2
    exit 1
fi

"$SCRIPT_DIR/install.sh"

PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m agent_issues.cli.init_repo --agents --claude --hook "$REPO_ROOT"

echo ""
echo "Repository opted into agent-issues:"
echo "  $REPO_ROOT"
