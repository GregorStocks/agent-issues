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

alias cod='coding-agent-here codex --dangerously-bypass-approvals-and-sandbox'
alias cld='coding-agent-here claude --permission-mode=auto'
