#!/bin/sh
# Source this file to get the `worktree-new`, `worktree-rm`, and `worktree-unrm` functions.
# Compatible with both bash and zsh.
#   source /path/to/worktree.sh

if [ -n "$ZSH_VERSION" ]; then
    _WORKTREE_SH_DIR="${0:A:h}"
elif [ -n "$BASH_VERSION" ]; then
    _WORKTREE_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

worktree-new() {
    local worktree_base="$HOME/code/worktrees"

    if ! git rev-parse --is-inside-work-tree &>/dev/null; then
        echo "error: not inside a git repository" >&2
        return 1
    fi

    local branch_name="${1:-$(_new_worktree_random_name)}"
    local worktree_path="$worktree_base/$branch_name"

    mkdir -p "$worktree_base"

    git fetch origin master

    if ! git worktree add "$worktree_path" -b "$branch_name" origin/master; then
        return 1
    fi

    # sync claude local settings into the new worktree
    local source_root
    source_root="$(git -C "$worktree_path" rev-parse --path-format=absolute --git-common-dir)/.."
    if [ -f "$source_root/.claude/settings.local.json" ]; then
        mkdir -p "$worktree_path/.claude"
        cp "$source_root/.claude/settings.local.json" "$worktree_path/.claude/settings.local.json"
    fi

    cd "$worktree_path"

    if [ -n "$TMUX" ]; then
        tmux rename-window -t "$TMUX_PANE" "$branch_name"
    fi

    if [ -x "scripts/worktree-setup.py" ]; then
        ./scripts/worktree-setup.py
    fi
}

worktree-rm() {
    local worktree_base="$HOME/code/worktrees"

    if ! git rev-parse --is-inside-work-tree &>/dev/null; then
        echo "error: not inside a git repository" >&2
        return 1
    fi

    local name="$1"
    if [ -z "$name" ]; then
        case "$PWD" in
            "$worktree_base"/*)
                name="$(basename "$PWD")"
                ;;
            *)
                echo "error: specify a worktree name (or cd into one under $worktree_base)" >&2
                return 1
                ;;
        esac
    fi
    local worktree_path="$worktree_base/$name"

    if [ ! -d "$worktree_path" ]; then
        echo "error: worktree not found: $worktree_path" >&2
        return 1
    fi

    # if we're inside the worktree being removed, cd out first
    if [ "$(pwd)" = "$(realpath "$worktree_path")" ]; then
        cd "$(git -C "$worktree_path" rev-parse --path-format=absolute --git-common-dir)/.."
    fi

    # check for claude settings to merge back to the main repo
    local source_root
    source_root="$(git -C "$worktree_path" rev-parse --path-format=absolute --git-common-dir)/.."
    local wt_settings="$worktree_path/.claude/settings.local.json"
    local main_settings="$source_root/.claude/settings.local.json"

    if [ -f "$wt_settings" ]; then
        if [ -f "$main_settings" ]; then
            local new_items
            new_items="$(_claude_settings_diff "$main_settings" "$wt_settings")"
            if [ -n "$new_items" ]; then
                echo "The worktree has new claude local settings not in the main repo:"
                echo "$new_items"
                echo
                printf "Merge into main repo settings? [y/N] "
                read -r answer
                if [[ "$answer" =~ ^[Yy] ]]; then
                    _claude_settings_merge "$main_settings" "$wt_settings"
                    echo "Merged."
                fi
            fi
        else
            echo "The worktree has claude local settings but the main repo doesn't."
            printf "Copy them to the main repo? [y/N] "
            read -r answer
            if [[ "$answer" =~ ^[Yy] ]]; then
                mkdir -p "$source_root/.claude"
                cp "$wt_settings" "$main_settings"
                echo "Copied."
            fi
        fi
    fi

    # save commit hash for undo
    local tombstone_dir="$(git rev-parse --git-common-dir)/worktree-tombstones"
    mkdir -p "$tombstone_dir"
    git -C "$worktree_path" rev-parse HEAD > "$tombstone_dir/$name"

    git worktree remove "$worktree_path" && git branch -d "$name" 2>/dev/null

    if [ -n "$TMUX" ]; then
        local wt_window
        wt_window="$(tmux list-windows -F '#{window_id} #{window_name}' | awk -v n="$name" '$2 == n { print $1; exit }')"
        if [ -n "$wt_window" ]; then
            tmux set-option -w -t "$wt_window" automatic-rename on
        fi
    fi
}

worktree-unrm() {
    local worktree_base="$HOME/code/worktrees"

    if ! git rev-parse --is-inside-work-tree &>/dev/null; then
        echo "error: not inside a git repository" >&2
        return 1
    fi

    local name="$1"
    if [ -z "$name" ]; then
        echo "usage: undo-rm-worktree <branch-name>" >&2
        return 1
    fi

    local tombstone_dir="$(git rev-parse --git-common-dir)/worktree-tombstones"
    local tombstone="$tombstone_dir/$name"
    if [ ! -f "$tombstone" ]; then
        echo "error: no tombstone found for '$name'" >&2
        return 1
    fi
    local commit
    commit="$(cat "$tombstone")"

    local worktree_path="$worktree_base/$name"
    mkdir -p "$worktree_base"
    if git worktree add "$worktree_path" -b "$name" "$commit"; then
        rm "$tombstone"

        # sync claude local settings into the restored worktree
        local source_root
        source_root="$(git -C "$worktree_path" rev-parse --path-format=absolute --git-common-dir)/.."
        if [ -f "$source_root/.claude/settings.local.json" ]; then
            mkdir -p "$worktree_path/.claude"
            cp "$source_root/.claude/settings.local.json" "$worktree_path/.claude/settings.local.json"
        fi

        cd "$worktree_path"

        if [ -n "$TMUX" ]; then
            tmux rename-window -t "$TMUX_PANE" "$name"
        fi
    else
        return 1
    fi
}

_claude_settings_diff() {
    if ! command -v jq &>/dev/null; then
        echo "error: jq is required for settings merge but is not installed" >&2
        return 1
    fi
    local main="$1" worktree="$2"
    jq -n --slurpfile main "$main" --slurpfile wt "$worktree" '
        def array_diff(a; b): [b[] | select(. as $item | a | index($item) | not)];
        def obj_diff(a; b):
            reduce (b | keys[]) as $key ({};
                if (a | has($key) | not) then
                    .[$key] = b[$key]
                elif (a[$key] | type) == "array" and (b[$key] | type) == "array" then
                    array_diff(a[$key]; b[$key]) as $d |
                    if ($d | length) > 0 then .[$key] = $d else . end
                elif (a[$key] | type) == "object" and (b[$key] | type) == "object" then
                    obj_diff(a[$key]; b[$key]) as $d |
                    if ($d | length) > 0 then .[$key] = $d else . end
                else .
                end
            );
        obj_diff($main[0]; $wt[0]) |
        if . == {} then empty else . end
    '
}

_claude_settings_merge() {
    if ! command -v jq &>/dev/null; then
        echo "error: jq is required for settings merge but is not installed" >&2
        return 1
    fi
    local main="$1" worktree="$2"
    local merged
    merged="$(jq -n --slurpfile main "$main" --slurpfile wt "$worktree" '
        def deep_merge(a; b):
            if b == null then a
            elif a == null then b
            elif (a | type) == "object" and (b | type) == "object" then
                reduce ([a, b] | add | keys[]) as $key ({};
                    .[$key] = deep_merge(a[$key]; b[$key])
                )
            elif (a | type) == "array" and (b | type) == "array" then
                (a + b) | unique
            else b
            end;
        deep_merge($main[0]; $wt[0])
    ')" && echo "$merged" > "$main"
}

_new_worktree_random_name() {
    local dict="$_WORKTREE_SH_DIR/eff_wordlist.txt"
    if [[ -f "$dict" ]]; then
        shuf -n 2 "$dict" | paste -sd '-'
    else
        printf '%04x-%04x' $((RANDOM * RANDOM)) $((RANDOM * RANDOM))
    fi
}
