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

## Submit Hooks

Repos that need local automation on every PR submit can add
`.agent-issues/submit-hooks.json5`:

```json5
{
  prepare: ["make agent-submit-prepare"],
  after_publish: ["make agent-submit-after-publish"],
}
```

`agent-submit` runs `prepare` commands after its basic clean-tree preflight and
before pushing. Use this phase for repo-owned validation, generated artifact
refreshes, and commits of intentional generated diffs. A prepare hook may change
HEAD by creating commits, but it must finish on the same branch with a clean
working tree.

`agent-submit` runs `after_publish` commands after pushing and creating or
updating the PR, but before CI/review watching. Use this phase for signoff or
status creation that needs the final pushed SHA. The hook receives
`AGENT_SUBMIT_SHA`, `AGENT_SUBMIT_BRANCH`, `AGENT_SUBMIT_BASE`,
`AGENT_SUBMIT_PR_NUMBER`, `AGENT_SUBMIT_PHASE`, and `AGENT_SUBMIT_REPO_ROOT`.
An after-publish hook must not change branches, change HEAD, or leave a dirty
working tree.

Do not teach agents to bypass submit hooks with raw `git push`, `gh pr edit`,
or direct signoff commands. Put the repo-specific automation behind hook
commands and let agents use `agent-submit`.

## Local Skill Templates

Repo-local skills should contain only the parts that are specific to that
repository. Keep PR creation, PR updates, CI watching, review feedback loops,
timeout handling, local issue claiming, and GitHub Issue policy in the shared
`create-pr`, `solve-issue`, `submit-pr`, and `agent-issues` skills.

Use this shape for `.claude/skills/create-pr-local/SKILL.md` or
`.agents/skills/create-pr-local/SKILL.md`:

```markdown
---
name: create-pr-local
description: Repo-specific instructions to use alongside the global create-pr skill.
---

# Create a Pull Request: Local Notes

Use these notes together with the global `create-pr` skill.

## Pre-Validation

- Read `Makefile` first and prefer existing project targets over direct tool
  invocations.
- Before opening a PR, run `<repo test command>` and `<repo lint command>`.
- If you change generated-output inputs, run `<regeneration command>` and
  commit intentional generated diffs.
- If `.agent-issues/submit-hooks.json5` owns validation or generated artifact
  commits, say that agents should rely on `agent-submit` for those steps.

## Repo-Specific Constraints

- Do not hand-edit generated files under `<generated path>`.
- If validation dirties tracked files, inspect the diff and commit intentional
  artifacts before creating the PR.
- If adding a dependency, document the repo-specific selection criteria and any
  version policy that reviewers expect.
```

Use this shape for `.claude/skills/solve-issue-local/SKILL.md` or
`.agents/skills/solve-issue-local/SKILL.md`:

```markdown
---
name: solve-issue-local
description: Repo-specific instructions to use alongside the global solve-issue skill.
---

# Solve an Issue: Local Notes

Use these notes together with the global `solve-issue` skill.

## Repo-Specific Commands

- Baseline verification is `<repo test command>` and `<repo lint command>`.
- If you touch `<generated input path>`, run `<regeneration command>` and
  inspect the generated diff before committing it.
- If a targeted check exists for the touched area, run it before the full suite.
- If `.agent-issues/submit-hooks.json5` owns validation or generated artifact
  commits, say that agents should rely on `agent-submit` for those steps.

## Repo-Specific Constraints

- Follow `<domain rule>` when changing `<area>`.
- Do not hand-edit generated files under `<generated path>`.
```

Good local skills may also list dependency approval requirements, generated
artifact commit hygiene, product/domain constraints, or project-specific review
steps. They should not describe how to call `agent-submit`, how to update PR
metadata, how to interpret watcher exit codes, how many CI/review loops to run,
or whether to create GitHub Issues unless the repo deliberately overrides the
shared policy. They also should not tell agents to run direct signoff/status
commands when submit hooks can own that automation.

## Temporary Files And Logs

Put repository-local temporary files under `tmp/` in the worktree unless the
repo says otherwise. Avoid `/tmp` on hosts where tmpfs space is constrained.

Put repository-local logs under `logs/` unless the repo defines a more specific
log location.
