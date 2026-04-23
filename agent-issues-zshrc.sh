#!/bin/sh
# Source this file from ~/.zshrc to get `worktree-new`, `worktree-rm`,
# and `worktree-unrm` wrappers that cd into the resulting worktree, plus
# convenience aliases for launching coding agents in the right place.
#
#   source /path/to/agent-issues-zshrc.sh

worktree-new() {
    local target
    target="$(command worktree-new "$@")" || return $?
    [ -n "$target" ] && cd "$target"
}

worktree-rm() {
    local target
    target="$(command worktree-rm "$@")" || return $?
    [ -n "$target" ] && cd "$target"
}

worktree-unrm() {
    local target
    target="$(command worktree-unrm "$@")" || return $?
    [ -n "$target" ] && cd "$target"
}

_agent-new-worktree-if-main() {
    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
    [ "$(git rev-parse --path-format=absolute --git-dir)" = \
      "$(git rev-parse --path-format=absolute --git-common-dir)" ] || return 0
    worktree-new
}

cod() { _agent-new-worktree-if-main && codex --dangerously-bypass-approvals-and-sandbox "$@"; }
cld() { _agent-new-worktree-if-main && claude --permission-mode=auto "$@"; }
