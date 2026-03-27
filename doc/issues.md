# Issues

Issues are stored as individual JSON5 files in the `issues/` directory at the root of a repository. The filename serves as the issue ID and must start with `p1-`, `p2-`, `p3-`, `p4-`, or `blocked-` (e.g., `p3-fix-login-redirect.json5`).

For intentionally related issue series, include a stable sequencing token in the filename after that prefix so `ls issues/` keeps the set grouped and ordered. Example: `blocked-migration-step5.json5` and later `p3-migration-step5.json5`.

Resolved issues should be deleted, not marked as resolved/closed.

## Format

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

Use real timestamps (the actual time you're creating the issue), not `00:00:00` placeholders.

### Fields

| Field | Type | Description |
| ------- | ------ | ------------- |
| `title` | string | Short summary |
| `description` | string | Full description with context |
| `status` | string | Always "open" (delete closed issues) |
| `priority` | int | 1 (highest) to 4 (lowest) |
| `type` | string | Usually "task" |
| `labels` | string[] | Tags for categorization |
| `created_at` | string | ISO 8601 timestamp |
| `updated_at` | string | ISO 8601 timestamp |
| `blocked` | bool \| string? | If truthy, the filename must start with `blocked-` and `issue-autoclaim` skips this issue. When a string, it describes *why* the issue is blocked (e.g. `"Waiting for upstream dependency to be fixed"`). |

## CLI Tools

If [agent-issues](https://github.com/GregorStocks/agent-issues) is installed, these commands are available:

### List all issues with priority

```bash
issue-query
```

### Filter by label

```bash
issue-query --label backend
```

### Show high priority issues (P1-P2)

```bash
issue-query --max-priority 2
```

### Search titles and descriptions

```bash
issue-query --search "streaming"
```

### Claim an issue

```bash
issue-autoclaim              # auto-pick highest priority unclaimed
issue-autoclaim <issue-name> # claim a specific issue
issue-claim <issue-name>     # claim without merging default branch first
issue-claim --current        # show current claim
issue-claim --list           # list all active claims
```

### Finalize a PR

```bash
issue-finalize-pr --title "Fix login redirect" --body "..."
```

### Watch CI

```bash
issue-watch-pr [<pr-number>]
```

### Lint issues

```bash
issue-lint [<project-root>]
```

## Script launcher notes

The `bin/issue-*` commands use a small shell/Python polyglot preamble instead of a
shebang like `#!/usr/bin/env -S uv run ...`.

That is intentional:

- macOS ships BSD `env`, and its `-S` handling is not portable enough for these launchers
- shebangs do not expand shell variables like `${HOME}`, so paths in the interpreter
  line are brittle even when `env -S` exists
- the polyglot preamble keeps the files directly executable, runs them through
  `uv run --project ...` when `uv` is installed, and still lets the test suite import
  the scripts as plain Python modules
