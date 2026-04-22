---
name: submit-pr
description: Push the current branch, open or update its PR, and loop through CI failures and review feedback until the PR is clean.
---

# Submit a Pull Request

Push HEAD, open or update the branch's PR, and keep iterating — fix CI failures and address review feedback — until the PR is ready. Compose a fresh title and body from the current branch state on every iteration so the PR description tracks the latest commits.

## Arguments

Callers pass zero or more issue identifiers that this PR resolves:

```
/submit-pr [issue-id ...]
```

Examples:
- `/submit-pr` — no issue context (e.g., invoked from `create-pr` for a general change).
- `/submit-pr p2-fix-timeout` — this PR resolves a claimed issue (stem form, as returned by `issue-claim --current`).
- `/submit-pr p2-fix-timeout.json5` — filename form also accepted.
- `/submit-pr issues/p2-fix-timeout.json5` — path form also accepted.

**Normalize each argument to a stem** (strip any `issues/` prefix and any `.json5` suffix). The issue file may have been renamed across `blocked-…`/`pN-…` prefixes during the branch's lifetime, so don't assume the exact basename you were given still exists on disk. For each stem:

1. Try the working tree: `ls issues/*<stem>*.json5` and pick the unique match if one exists.
2. Otherwise recover from git history. Resolve the default branch first (`DEFAULT=$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)`), then run `git log -p "origin/$DEFAULT..HEAD" -- 'issues/*<stem>*.json5'`. The branch's deletion (or rename) commit carries the final content.
3. If neither locates the file, report to the user which argument failed rather than silently skipping it.

## Workflow

1. **Commit any uncommitted work.** Run `git status`. If there are staged or unstaged changes, commit them before pushing — everything that belongs to this PR must be in a commit.

2. **Read the branch state.** Run `branch-summary` to fetch origin and see the commits ahead of the default branch plus the diff stat. Read the commit messages and the diff, not just filenames — you need enough context to write a PR description that explains *why* these changes exist.

3. **Compose a fresh title and body each iteration.** Do not reuse a prior iteration's text verbatim; new commits (CI fixes, review responses) should be reflected.

   **Title**: short, imperative, under 70 characters. Describe the outcome, not the mechanism (e.g., "Fix timeout for slow models" not "Add timeout_secs config parameter").

   **Body**:

   ```
   ## Summary
   <2-5 bullets mixing why and what — lead with motivation>

   ## Test plan
   <bulleted checklist of what you actually verified>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

   Start with the problem or motivation, then the solution. A reader should understand why this PR exists from the first bullet alone. The test plan lists specific commands, manual testing, or scenarios you actually ran — don't list things you didn't do.

   **If issue arguments were passed**, add a short **Issue context** section immediately above `## Summary`. Write it for a reader who may not remember the issue they filed days earlier: what the bug/task was, how it showed up, and why this change fixes it. Pull from the issue file (working tree or git history).

4. **Run `agent-submit`**. It pushes HEAD, creates or updates the PR with your title/body, and runs the CI watcher end-to-end:

   ```bash
   agent-submit --title "<title>" --body "$(cat <<'EOF'
   <body>
   EOF
   )"
   ```

5. **Interpret the exit code:**

   | Code  | Meaning                                                                                   |
   |-------|-------------------------------------------------------------------------------------------|
   | 0     | All clean. Done — exit the loop.                                                          |
   | 1     | CI failed or merge conflict. If merge conflict, merge the default branch and resolve. Otherwise use `gh run view <run-id> --log-failed` (ID from the printed link) to find the failure, fix the root cause, commit, then loop back to step 1. |
   | 2     | Review feedback arrived. Read the printed feedback; for inline comments fetch full context with `gh api repos/{owner}/{repo}/pulls/{number}/comments`. Address each comment, commit, then loop back to step 1. |
   | 4     | Watcher timed out — **terminal**. Do not re-run automatically; stop and tell the user. |
   | 10+   | Preflight failed (on default branch, dirty working tree, not a git repo, etc.). Fix and loop back to step 1. |

6. **Cap at 10 iterations.** If after 10 fix-and-resubmit rounds CI still fails or new feedback keeps arriving, report the situation to the user and stop.

## Guidelines

- **Root-cause fixes only.** When CI fails, diagnose the actual failure — don't paper over it with a broader timeout, a skipped test, or a `try/except` that swallows the error.
- **One logical change per PR** — if a CI failure or review comment reveals work that belongs in a separate PR, note it for the user rather than bundling it in.
- **Never skip hooks** (`--no-verify`) or bypass signing to make `agent-submit` pass. If a hook fails, fix the underlying issue.
