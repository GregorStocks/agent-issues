# Issues

Issues are stored as individual JSON5 files in the `issues/` directory at the root of a repository. The filename serves as the issue ID and must start with `p0-`, `p1-`, `p2-`, `p3-`, `p4-`, or `blocked-` (e.g., `p3-fix-login-redirect.json5`).

For intentionally related issue series, include a stable sequencing token in the filename after that prefix so `ls issues/` keeps the set grouped and ordered. Example: `blocked-migration-step5.json5` and later `p3-migration-step5.json5`.

Resolved issues should be deleted, not marked as resolved/closed.

## Format

Write prose with one sentence per source line, without a fixed column limit.
Preserve paragraph breaks, lists, and code blocks.
Run `issue-fmt` to format JSON5 strings with line continuations at sentence boundaries while preserving their exact values.
The formatter recognizes sentence punctuation followed by spaces and a capital letter, optionally with surrounding quotes, parentheses, or Markdown delimiters, while excluding initials and common abbreviations.
Ambiguous prose may need explicit newlines.

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
| `priority` | int | 0 (highest) to 4 (lowest) |
| `type` | string | Usually "task" |
| `labels` | string[] | Tags for categorization |
| `created_at` | string | ISO 8601 timestamp |
| `updated_at` | string | ISO 8601 timestamp |
| `blocked` | bool \| string? | If truthy, the filename must start with `blocked-` and `issue-autoclaim` skips this issue. When a string, it describes *why* the issue is blocked (e.g. `"Waiting for upstream dependency to be fixed"`). |

## CLI Tools

Install the CLI with `uv tool install --editable /path/to/agent-issues`.

If the tool executable directory is not already on your `PATH`, run `uv tool update-shell`
or inspect it with `uv tool dir --bin`.

Once installed, these commands are available:

### Bootstrap a repository

```bash
agent-issues init [--dry-run] [--agents] [--claude] [--hook]
agent-issues-init [--dry-run] [--all]
```

By default this creates only `issues/.gitignore`, leaving the directory present
without changing ignore behavior. Optional flags add repo guidance includes,
local skill-note templates, shared pre-tool hook files, or Makefile/CI snippets.
Makefile snippets are created only when no GNU makefile already exists. The
command skips existing custom files and generated files that have diverged from
the default template.

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
agent-submit --title "Fix login redirect" --body "..."
```

### Watch CI

```bash
issue-watch-pr [<pr-number>]
```

### Lint issues

```bash
issue-lint [<project-root>]
```

These commands are exposed through `[project.scripts]` entrypoints in
`pyproject.toml`, so `uv` generates the platform-native launchers for macOS, Linux,
and Windows.
