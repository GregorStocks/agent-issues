# Agent Instructions

This repository implements the `agent-issues` CLI and shared agent skills.

Use the global `agent-issues` skills for issue claiming and PR submission workflow.
Keep this file limited to repo-specific guidance.

## Prose Formatting

Write Markdown and other prose, including issue descriptions, with one sentence per source line.
Do not hard-wrap prose at 80 columns or another fixed width.
Preserve paragraph breaks, list structure, tables, and code blocks.
When editing existing prose, apply this convention to the paragraphs you touch.
Use `issue-fmt` for JSON5 line continuations that preserve the stored text.

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

Do not create, close, or comment on GitHub Issues for this repository unless Gregor explicitly asks.
