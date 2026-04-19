# `agent-submit` — unified push / PR-update / CI-watch helper

## Background

Consumer repos' `AGENTS.md` files require that every push to a non-default
branch do three things, in order:

1. `git push origin HEAD`
2. Update the PR title and body to reflect the full diff against the base
   branch.
3. Run the CI watcher and react to its outcome.

Today this is three separate commands an agent has to remember to issue in
sequence. When the sequence is skipped, the PR drifts out of sync with the
branch, or CI failures go unnoticed. The goal of this project is to make
the three steps into a single command that is the only sanctioned push path
for these repos.

## Scope

This design covers:

- Adding a new CLI, `agent-submit`, to the `agent-issues` package.
- Removing the existing `issue-finalize-pr` CLI in favor of it.
- Updating in-repo skills (`create-pr`, `solve-issue`) and docs to reference
  the new command.

Out of scope:

- Modifying consumer repos' `AGENTS.md` files (that's where the "use
  `agent-submit` for all pushes" rule lives — this design only makes the
  command exist).
- Providing a deprecation shim for `issue-finalize-pr`. It's a personal
  workflow tool; a clean rename is fine.

## Command surface

```
agent-submit --title "<title>" --body "<body>" [--draft] [--base <branch>]
```

- `--title` (required): PR title, concise, imperative, under 70 characters.
- `--body` (required): PR body, describing the *why* of the full diff
  against the base branch. Required on every invocation — this is the
  enforcement mechanism for "keep the PR description current."
- `--draft` (optional): create the PR as a draft. Default is non-draft. On
  update calls, this flag is ignored (don't flip draft state on every push).
- `--base` (optional): base branch for new PRs. Default is the repo's
  default branch as reported by `gh repo view`.

### Exit codes

| Code | Meaning                                                          |
|------|------------------------------------------------------------------|
| 0    | Push + PR + watcher all clean.                                   |
| 1    | CI failed or PR has a merge conflict. Fix and re-run.            |
| 2    | Review feedback received. Address and re-run.                    |
| 3    | Both CI failures and review feedback.                            |
| 4    | Watcher timed out. **Terminal — do not re-run; wait for user.**  |
| 10   | Preflight guard failed (not a git repo, on default branch, dirty |
|      | working tree, `gh` not authenticated).                           |

Codes 0–4 are relayed verbatim from the existing `issue-watch-pr` logic.
Code 10 is new and distinguishable from watcher outcomes.

### Output

- Prints the PR URL after the push/edit step.
- Streams the watcher's progress (same lines `issue-watch-pr` prints today).
- On non-zero exit, prints a tailored "NEXT STEP" footer:
  - `1` → "CI failed or merge conflict. Investigate with `gh run view ...`,
    fix, then re-run `agent-submit`."
  - `2` → "Review feedback received. Address comments, then re-run
    `agent-submit`."
  - `3` → "Both CI failures and review feedback. Address both, then re-run
    `agent-submit`."
  - `4` → "Watcher timed out — likely all fine but didn't confirm. Do not
    re-run automatically; stop and wait for the user."
  - `10` → describes the specific preflight violation.

## Behavior

### Preflight guards (exit 10 on failure)

1. Current working directory is inside a git repo.
2. Current branch is not the repo's default branch.
3. Working tree is clean (`git status --porcelain` empty).
4. `gh` is authenticated. Deferred check — let the first `gh` call fail
   with its native error rather than adding a round-trip upfront.

### Steps

1. **Push.** `git push origin HEAD`. If the branch has no upstream, add
   `-u`. If `git push` exits non-zero, `agent-submit` exits with that code
   (not remapped to 10 — this is a legitimate git error, not a preflight
   violation) and does not proceed to steps 2 or 3.
2. **Create or update PR.**
   - Query `gh pr list --head <branch> --state open --json number` to find
     an existing open PR. If the query returns more than one open PR for
     the branch, abort with exit 10 (data violates the one-PR-per-branch
     invariant and needs human attention).
   - If none: `gh pr create --base <base> --title ... --body ...`. Add
     `--draft` if the flag was passed. Capture the resulting PR number.
   - If one: `gh pr edit <num> --title ... --body ...`. Do not touch draft
     state. Reuse `<num>` for step 3.
   - Print the PR URL.
3. **Watch.** Call the extracted `issue_watch_pr.run(pr=<num>)` in-process
   with the PR number captured in step 2 (don't let the watcher re-query).
   Capture the exit code. Print the NEXT-STEP footer if non-zero. Exit
   with the watcher's code.

### Refactor of `issue_watch_pr`

Extract a `run(pr: str | None) -> int` function from
`issue_watch_pr.main()` that does all the polling and returns an exit code
instead of calling `sys.exit()`. `main()` becomes a thin wrapper that calls
`run()` and exits with its return value. `agent-submit` calls `run()`
directly so it can print its footer before exiting.

This refactor preserves the existing CLI and its tests.

## Migration

- Rename `agent_issues/cli/issue_finalize_pr.py` →
  `agent_issues/cli/agent_submit.py`. Rewrite to match this design (drop
  the claim check; add the watcher call; add preflight guards).
- Update `pyproject.toml` `[project.scripts]`: remove `issue-finalize-pr`;
  add `agent-submit = "agent_issues.cli.agent_submit:main"`.
- `skills/create-pr/SKILL.md`: replace steps 8–10 with a single step that
  invokes `agent-submit --title ... --body ...`. Add the exit-code table
  and the "exit 4 is terminal" note so agents know how to react.
- `skills/solve-issue/SKILL.md`: swap `issue-finalize-pr` references for
  `agent-submit` (lines ~108, ~129, ~138). Remove the note about the claim
  store, since `agent-submit` doesn't read it.
- `doc/issues.md`: swap the `issue-finalize-pr` reference at line 86.
- No deprecation shim.

## Testing

- `tests/test_agent_submit.py`:
  - Preflight guards: default-branch, dirty-tree, not-a-git-repo. Mock
    `subprocess.run`.
  - PR create-vs-update branching: mock `gh pr list` returning `[]` vs
    `[{number: 5}]`; assert the right `gh` call is issued.
  - Watcher integration: mock the extracted
    `issue_watch_pr.run(pr) -> int`; verify `agent-submit` relays exit
    codes and prints the right NEXT-STEP footer for 1/2/3/4.
  - `--draft` flag reaches `gh pr create` on create; is ignored on update.
- Existing `tests/test_watch_pr.py` stays and passes after the `run()`
  extraction.
- No end-to-end tests that hit GitHub.
