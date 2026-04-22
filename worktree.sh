#!/bin/sh
# Source this file to get `worktree-new`, `worktree-rm`, and `worktree-unrm`
# shell wrappers that cd into the worktree after the Python CLI does its work.
# Compatible with both bash and zsh.
#   source /path/to/worktree.sh

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
