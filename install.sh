#!/bin/bash
# Install agent-issues: symlink skills into Claude Code and Codex skill directories.
#
# Usage:
#   ./install.sh
#
# After running, add the bin directory to your PATH:
#   export PATH="$HOME/code/agent-issues/bin:$PATH"

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DIR="$SCRIPT_DIR/skills"

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

echo ""
echo "Skills installed. Add agent-issues to your PATH:"
echo ""
echo "  export PATH=\"$SCRIPT_DIR/bin:\$PATH\""
