# Shared Consumer Setup Plan

## Context

`library-of-leng`, `infallible-record`, and `ramekin` are all using the
`agent-issues` workflow. The setup PR for `infallible-record`
(`GregorStocks/infallible-record#18`) added 11 files and 273 lines to introduce
repo guidance, local skills, issue docs, issue scaffolding, CI/check glue, and
Claude skill symlinks.

That PR is a useful baseline because most of its agent-facing text is not
specific to `infallible-record`.

## Findings

- `create-pr-local` repeats publication workflow that already belongs in the
  global `create-pr` and `submit-pr` skills: use `agent-submit`, describe the
  full diff, avoid raw `git push` / `gh pr edit`, interpret watcher timeouts,
  and loop on CI or review feedback.
- `solve-issue-local` is closer to the right shape. It mostly carries
  repo-specific validation commands, generated-output policy, and content/copy
  constraints.
- `AGENTS.md` files repeat the same framework rules: local JSON5 issues,
  delete resolved issues, avoid GitHub Issues, stay on the agent branch, use
  `agent-submit`, and use local `tmp/` / `logs/`.
- `docs/issues.md` / `doc/issues.md` copies are mostly the same framework
  schema and CLI command list, with repo-specific priority examples mixed in.
  They are already inconsistent about details such as whether P0 exists.
- Pre-tool hooks in `library-of-leng` and `ramekin` enforce the same broad
  policy family, but each repo carries a large bespoke Python implementation.
  The repeated parts are command parsing, raw PR publishing blocks, GitHub Issue
  blocks, branch-switch safeguards, timeout checks, kill-by-name blocks, and
  generated-output protection. The repo-specific parts are path lists, make
  target names, signoff environment variables, and project-specific binary
  blocks.

## Recommended Split

1. **Make `agent-issues` itself a consumer.** Add repo-local agent instructions
   and an `issues/` queue here so future framework work is tracked the same way
   as consumer work.

2. **Shrink local skills to true local notes.** Keep the full PR and watcher
   workflow in the shared `create-pr` / `submit-pr` skills. Local
   `create-pr-local` files should only list repo validation commands,
   generated-artifact rules, and any repo-specific pre-submit checks.

3. **Publish shared agent instructions.** Add a shared markdown file in this
   repo for framework-level conventions: issue schema, command list, PR
   workflow, branch policy, and standard temp/log guidance. Teach `install.sh`
   to insert a single include line into the user's global Claude/Codex agent
   file if absent, without replacing existing user content.

4. **Leave repo `AGENTS.md` files repo-specific.** Consumer repos should stop
   restating `agent-issues` behavior. They should contain project guidance,
   validation commands, generated-artifact policy, and content/copy constraints.
   Repo-specific issue priority rubrics can remain, but schema and CLI command
   documentation should point at the shared doc.

5. **Extract a shared pre-tool hook runner.** Add an `agent-pretool-hook` CLI
   that reads hook input from stdin and evaluates shared rules from a typed
   config file. Move the robust shell parser and generic Git/GitHub guards into
   `agent-issues`; keep only declarative repo config and exceptional local hook
   code in consumer repos.

6. **Add a consumer bootstrap command.** Add `agent-issues init` or equivalent
   to create minimal repo scaffolding non-destructively: `issues/.gitkeep`,
   optional `AGENTS.md` / `CLAUDE.md`, optional local skill notes, optional hook
   settings, and optional Makefile/CI snippets. It should support dry-run and
   refuse to overwrite non-generated content.

7. **Migrate consumers in small PRs.** For each existing consumer repo, split
   migration into separate PRs: shared instruction include, local skill shrink,
   hook runner adoption, and issue-doc cleanup. That keeps each PR focused and
   avoids repeating the size of `infallible-record#18`.

## Tracked Follow-Ups

The local issue queue encodes the implementation order with
`consumer-setup-stepN` filename tokens:

1. `issues/p2-consumer-setup-step1-self-validation.json5`
2. `issues/p2-consumer-setup-step2-shared-agent-instructions.json5`
3. `issues/p2-consumer-setup-step3-shrink-local-skills.json5`
4. `issues/p2-consumer-setup-step4-shared-pretool-hook-framework.json5`
5. `issues/p3-consumer-setup-step5-init-command.json5`
6. `issues/p3-consumer-setup-step6-migrate-existing-consumers.json5`

The migration target issue names the existing consumer checkouts explicitly:
`/home/gregor/code/library-of-leng`, `/home/gregor/code/infallible-record`, and
`/home/gregor/code/ramekin`.
