#!/bin/bash
# Install agent-issues skills and CLI commands.
#
# Usage:
#   ./install.sh
#
# This installs the Python CLI with `uv tool install --editable`, then links
# the skills into Claude Code and Codex skill directories.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$SCRIPT_DIR/skills"

ensure_global_agent_line() {
    local file="$1"
    local agent_line="$2"

    mkdir -p "$(dirname "$file")"
    touch "$file"

    if grep -Fxq "$agent_line" "$file"; then
        echo "Agent line already present: $file"
        return
    fi

    if [ -s "$file" ]; then
        printf '\n%s\n' "$agent_line" >> "$file"
    else
        printf '%s\n' "$agent_line" >> "$file"
    fi
    echo "Added agent line: $file -> $agent_line"
}

if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv is required to install agent-issues." >&2
    exit 1
fi

uv tool install --force --editable "$SCRIPT_DIR"
UV_BIN_DIR="$(uv tool dir --bin)"

# Claude Code global skills
CLAUDE_SKILLS="$HOME/.claude/skills"
mkdir -p "$CLAUDE_SKILLS"

for skill_dir in "$SKILLS_DIR"/*/; do
    skill_name="$(basename "$skill_dir")"
    target="$CLAUDE_SKILLS/$skill_name"
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -e "$target" ]; then
        echo "Warning: $target exists and is not a symlink, skipping"
        continue
    fi
    ln -s "$skill_dir" "$target"
    echo "Linked: $target -> $skill_dir"
done

ensure_global_agent_line "$HOME/.claude/CLAUDE.md" "@$HOME/.claude/skills/agent-issues/SKILL.md"

# Codex global skills
CODEX_SKILLS="$HOME/.codex/skills"
mkdir -p "$CODEX_SKILLS"

for skill_dir in "$SKILLS_DIR"/*/; do
    skill_name="$(basename "$skill_dir")"
    target="$CODEX_SKILLS/$skill_name"
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -e "$target" ]; then
        echo "Warning: $target exists and is not a symlink, skipping"
        continue
    fi
    ln -s "$skill_dir" "$target"
    echo "Linked: $target -> $skill_dir"
done

ensure_global_agent_line "$HOME/.codex/AGENTS.md" 'Use the `agent-issues` skill when working in repositories that use the agent-issues local issue workflow.'

echo ""
echo "Skills installed."
echo ""
echo "CLI tools installed via uv."
echo "Tool bin dir: $UV_BIN_DIR"
echo ""
echo "If the commands are not on your PATH yet, run:"
echo ""
echo "  uv tool update-shell"
echo ""
echo "To get the shell wrappers plus cod/cld aliases in zsh, add this to ~/.zshrc:"
echo ""
echo "  source $SCRIPT_DIR/agent-issues-zshrc.sh"
