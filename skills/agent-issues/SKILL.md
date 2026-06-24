---
name: agent-issues
description: Use in repositories that track work with the agent-issues local issue workflow.
---

# Agent Issues Workflow

Use this skill when a repository uses `agent-issues` for local issue tracking,
agent branch workflow, and PR submission.

## Repository Instructions

Read the repository's `AGENTS.md` or equivalent local agent instructions first.
Those files should contain project-specific policy: validation commands,
generated-output rules, product constraints, and repo-specific priority rubrics.
The shared rules below describe the common `agent-issues` workflow.

## Local Issues

Issues live as individual JSON5 files under `issues/` in the repository root.
The filename is the issue ID and must start with `p0-`, `p1-`, `p2-`, `p3-`,
`p4-`, or `blocked-`.

Resolved issues are deleted in the PR that fixes them. Do not mark resolved
issues as closed in place.

Use a stable sequencing token in related issue filenames so `ls issues/` keeps
the series grouped and ordered, for example `blocked-migration-step5.json5`
and later `p3-migration-step5.json5`.

Issue files use this shape:

```json5
{
  "title": "Short summary of the issue",
  "description": "Full description with context...",
  "status": "open",
  "priority": 3,
  "type": "task",
  "labels": ["backend"],
  "created_at": "2026-02-09T14:30:00.000000-08:00",
  "updated_at": "2026-02-09T14:30:00.000000-08:00"
}
```

Use real timestamps, not placeholder times.

Supported fields:

| Field | Type | Description |
| --- | --- | --- |
| `title` | string | Short summary |
| `description` | string | Full description with context |
| `status` | string | Always `"open"` |
| `priority` | int | `0` highest through `4` lowest, unless a repo-specific rubric narrows this range |
| `type` | string | Usually `"task"` |
| `labels` | string[] | Tags for categorization |
| `created_at` | string | ISO 8601 timestamp |
| `updated_at` | string | ISO 8601 timestamp |
| `blocked` | bool \| string? | If truthy, filename must start with `blocked-` and `issue-autoclaim` skips it |

Create blocked issues when work requires human input or approval before an
agent can safely act. Use a `blocked` string that states the specific decision,
credential, external dependency, cost/rate-limit concern, or scope approval
needed.

Document follow-up work you discover by adding local issue files. Do not create,
close, or comment on GitHub Issues unless the user explicitly asks.

## Issue Commands

Use the shared CLI instead of repo-local scripts:

```bash
issue-query
issue-query --label backend
issue-query --max-priority 2
issue-query --search "streaming"

issue-autoclaim
issue-autoclaim <issue-name>
issue-claim <issue-name>
issue-claim --current
issue-claim --list

issue-fmt
issue-lint
```

`issue-autoclaim` is the normal claim path because it merges the default branch
first and respects the shared local claim store. `issue-claim` is for narrower
cases where the caller explicitly needs to claim without that merge.

After editing issue files, run:

```bash
issue-fmt
issue-lint
```

## Branch Policy

Agents usually work on a branch with two random English words in the name. Stay
on the current branch unless the user explicitly asks you to change branches.
Do not create replacement branches as part of normal issue or PR workflow.

Merge the default branch into the work branch when the workflow calls for
updates. Do not rebase or force-push unless the user explicitly approves that
operation.

If a working tree has unexpected changes, treat that as important context. Do
not silently discard, revert, or exclude files from the PR. Inspect the changes
and ask the user when ownership is unclear or the changes affect the task.

## PR Workflow

Use the shared PR skills when available:

- `solve-issue` claims exactly one issue, fixes it, deletes the issue file, and
  submits the PR.
- `create-pr` prepares and opens a PR for already-completed local changes.
- `submit-pr` pushes the branch, creates or updates the PR, and watches CI and
  review feedback through `agent-submit`.

For direct CLI use, create or update PRs with:

```bash
agent-submit --title "Updated title" --body "$(cat <<'EOF'
...updated description...
EOF
)"
```

`agent-submit` is the publishing path for both new PRs and updates. Do not use a
raw `git push` plus manual `gh pr edit` as a substitute for normal PR updates.
If CI fails or review feedback arrives, fix the root cause, commit, and submit
again.

## Temporary Files And Logs

Put repository-local temporary files under `tmp/` in the worktree unless the
repo says otherwise. Avoid `/tmp` on hosts where tmpfs space is constrained.

Put repository-local logs under `logs/` unless the repo defines a more specific
log location.
