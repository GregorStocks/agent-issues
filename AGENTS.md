# Agent Instructions

This repository implements the `agent-issues` CLI and shared agent skills.

Use the global `agent-issues` skills for issue claiming and PR submission
workflow. Keep this file limited to repo-specific guidance.

## Validation

Run the Python test suite before submitting changes:

```bash
uv run pytest
```

After editing local issue files, run:

```bash
uv run issue-fmt
uv run issue-lint
```

## Issues

Track follow-up work in `issues/*.json5` using the format in `doc/issues.md`.
Resolved issues are deleted in the PR that fixes them.

Do not create, close, or comment on GitHub Issues for this repository unless
Gregor explicitly asks.

