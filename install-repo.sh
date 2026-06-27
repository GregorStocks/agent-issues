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
HOOK_CONFIG="$REPO_ROOT/.agent-issues/pretool-hook.json5"
HOOK_SCRIPT="$REPO_ROOT/.claude/hooks/agent-issues-pretool-hook.sh"
CLAUDE_SETTINGS="$REPO_ROOT/.claude/settings.local.json"
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

ensure_file() {
    local file="$1"
    local mode="$2"
    local content="$3"

    if [ -e "$file" ]; then
        echo "File already present: $file"
        return
    fi

    mkdir -p "$(dirname "$file")"
    printf '%s\n' "$content" > "$file"
    chmod "$mode" "$file"
    echo "Created file: $file"
}

ensure_claude_pretool_hook() {
    mkdir -p "$(dirname "$CLAUDE_SETTINGS")"
    SETTINGS_FILE="$CLAUDE_SETTINGS" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

settings_path = Path(os.environ["SETTINGS_FILE"])
entry = {
    "matcher": "Bash",
    "hooks": [
        {
            "type": "command",
            "command": 'cd "${CLAUDE_PROJECT_DIR:-.}" && .claude/hooks/agent-issues-pretool-hook.sh',
        }
    ],
}

if settings_path.exists() and settings_path.read_text().strip():
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {settings_path}: {exc}", file=sys.stderr)
        sys.exit(1)
else:
    data = {}

hooks = data.setdefault("hooks", {})
pretool = hooks.setdefault("PreToolUse", [])
if entry not in pretool:
    pretool.append(entry)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Added Claude PreToolUse hook: {settings_path}")
else:
    print(f"Claude PreToolUse hook already present: {settings_path}")
PY
}

"$SCRIPT_DIR/install.sh"

ensure_line "$AGENTS_FILE" "$AGENT_ISSUES_LINE"
ensure_line "$CLAUDE_FILE" "@AGENTS.md"
ensure_file "$HOOK_CONFIG" "644" '{
  // Repo-specific settings for the shared agent-issues PreToolUse hook. This
  // hook is a convention guardrail for good-faith agents, not a security
  // sandbox. Keep normal project guidance declarative here; use
  // .claude/hooks/pretool-local.sh only for small repo-specific checks that do
  // not fit this config.
  //
  // Name of the environment variable an agent must set to the exact branch
  // name when a human has explicitly approved a git switch/checkout.
  branch_switch_signoff_env: "AGENT_BRANCH_SWITCH_SIGNOFF",

  // Repo-relative files or directories that are generated output. The hook
  // blocks direct shell edits to these paths and checks for dirty generated
  // output before publishing. Leave empty if the repo has no generated output.
  generated_paths: [],

  // Command agents should run when generated_paths need to be updated, for
  // example "make generate" or "uv run tools/build-generated.py".
  generated_command: "the generator target",

  // Human-readable description of the repo's issue tracker. This appears when
  // an agent tries to use gh issue commands.
  github_issue_guidance: "local JSON5 issue files in issues/",

  // Blocks whole command families such as raw package-manager or formatter
  // commands. Each entry has:
  //   command: executable basename to block, for example "cargo"
  //   message: guidance shown for any blocked use
  //   subcommands: optional map from subcommand to preferred replacement
  // Example:
  //   {command: "cargo", message: "Use Makefile targets.", subcommands: {test: "make test"}}
  command_family_blocks: [],

  // Blocks repo binaries by basename or fnmatch-style path pattern. Use this
  // when agents should run a wrapper instead of invoking built artifacts
  // directly. Example:
  //   {pattern: "target/*/project-cli", message: "Use make test."}
  binary_blocks: [],

  // Make targets that are implementation details. Map target name to the
  // public command agents should use instead, for example:
  //   {_generate: "make generate"}
  internal_make_targets: {},

  // Make targets that need a longer tool timeout. Values are milliseconds, for
  // example 4200000 for 70 minutes:
  //   {test: 4200000}
  make_targets_requiring_timeout_ms: {},

  // Minimum tool timeout for agent-submit, in milliseconds. Keep this long
  // enough for push, PR metadata updates, CI watching, and review polling.
  minimum_agent_submit_timeout_ms: 4200000,
}'
ensure_file "$HOOK_SCRIPT" "755" '#!/bin/sh
set -eu

tmp="$(mktemp)"
trap '\''rm -f "$tmp"'\'' EXIT
cat > "$tmp"

project_dir="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$project_dir"

local_hook="$project_dir/.claude/hooks/pretool-local.sh"
if [ -x "$local_hook" ]; then
    set +e
    "$local_hook" < "$tmp"
    local_status="$?"
    set -e
    if [ "$local_status" -ne 0 ]; then
        echo "Local pre-tool hook failed with status $local_status; blocking command." >&2
        exit 2
    fi
fi

hook_bin="$(command -v agent-pretool-hook || true)"
if [ -z "$hook_bin" ]; then
    uv_bin_dir="$(uv tool dir --bin 2>/dev/null || true)"
    hook_bin="$uv_bin_dir/agent-pretool-hook"
fi
if [ ! -x "$hook_bin" ]; then
    echo "agent-pretool-hook is not installed or not executable; blocking command." >&2
    exit 2
fi

"$hook_bin" --config "$project_dir/.agent-issues/pretool-hook.json5" < "$tmp"'
ensure_claude_pretool_hook

echo ""
echo "Repository opted into agent-issues:"
echo "  $REPO_ROOT"
