---
name: create-pr
description: Prepare and open a pull request for the current branch after validating the full diff and running checks.
---

# Create a Pull Request

Create a pull request for the current branch's changes.

## Repo-Specific Instructions

**Before starting**, check for a repo-scoped skill with additional instructions:
- `.claude/skills/create-pr-local/SKILL.md` (Claude Code)
- `.agents/skills/create-pr-local/SKILL.md` (Codex)

If either exists, read it and follow its instructions alongside this workflow. The local skill defines repo-specific validation commands, pre-PR steps, and any special considerations.

## Workflow

1. **Commit any uncommitted work.** Check `git status` — if there are staged or unstaged changes, commit them before proceeding. Everything that's part of this PR should be in a commit.

2. **Understand the full scope of changes.** Run these in parallel:

   ```bash
   git fetch origin
   git log --oneline origin/$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)..HEAD
   git diff origin/$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)..HEAD --stat
   ```

   Read through the actual diffs and changed files — don't just look at filenames. You need to understand what changed and why to write a good PR.

3. **Merge the default branch** so you're testing against the latest code:

   ```bash
   git merge --no-edit origin/$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
   ```

   Fix any merge conflicts before proceeding. Use `--no-edit` so repo merge settings do not drop you into an interactive editor mid-workflow.

4. **Format issues.** Run `issue-fmt` to auto-format issue files before validation.

5. **Run pre-validation steps** from the local skill if it exists (e.g., regenerate stale test fixtures, build generated code). Skip this step if no local skill is present.

6. **Run the validation suite.** Consult the local `create-pr-local` skill for the specific commands. If no local skill exists, look for `Makefile` targets like `make check`, `make test`, or `make lint`. Fix any failures before proceeding. Do not create a PR with failing checks.

   After validation, run `git status` again before pushing. Build and test commands can dirty tracked files. Commit intentional artifacts or clean incidental churn before you open the PR.

7. **Write the PR title and body.** The PR description must explain **why** these changes exist, not just what they do. A reviewer can read the diff to see *what* changed — the PR body should tell them *why* it changed, what problem it solves, and any context they'd need to evaluate the approach.

   Bad (just restates the diff):
   > - Add `timeout` parameter to `fetch_data()`
   > - Update `config.json` to include `timeout_secs` field
   > - Add test for timeout behavior

   Good (explains the motivation):
   > Request timeouts were too aggressive for slower models, causing a 32%
   > failure rate. Increase the timeout to 120s so they can finish without
   > getting cut off.

   The summary bullets should be a mix of what and why — lead with the motivation, then mention key implementation details only when they're non-obvious.

8. **Submit the PR.** Run `agent-submit` — it pushes HEAD, creates or updates the PR with the title/body you provide, and runs the CI watcher end-to-end:

   ```bash
   agent-submit --title "<concise title>" --body "$(cat <<'EOF'
   ## Summary
   <2-5 bullets mixing why and what>

   ## Test plan
   <bulleted checklist — what you verified>

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

   `agent-submit` exits with one of these codes:

   | Code  | Meaning                                                                                   |
   |-------|-------------------------------------------------------------------------------------------|
   | 0     | All clean. Done.                                                                          |
   | 1     | CI failed or merge conflict. If merge conflict, merge the default branch and resolve. Otherwise use `gh run view <run-id> --log-failed` (ID from the printed link), fix the root cause, commit, then re-run `agent-submit`. |
   | 2     | Review feedback arrived. Read the printed feedback; for inline comments fetch full context with `gh api repos/{owner}/{repo}/pulls/{number}/comments`. Address each, commit, re-run. |
   | 3     | Both CI failures and review feedback. Address both, then re-run.                          |
   | 4     | Watcher timed out — **terminal**. Do not re-run automatically; stop and wait for the user. |
   | 10+   | Preflight failed (on default branch, dirty working tree, not a git repo, etc.). Fix and re-run. |

   **Cap at 10 fix-and-resubmit iterations.** If after 10 rounds CI still fails or new feedback keeps arriving, report the situation to the user and stop.

## Guidelines

- **Title**: Short, imperative, under 70 characters. Describes the outcome, not the mechanism (e.g., "Fix timeout for slow models" not "Add timeout_secs config parameter").
- **Summary**: Start with the *problem* or *motivation*, then describe the solution. A reader should understand why this PR exists from the first bullet alone.
- **Test plan**: List what you actually verified — specific commands, manual testing, scenarios. Don't list things you didn't do.
- **One logical change per PR** — don't bundle unrelated work.
- If the branch has many commits, the PR description should synthesize the overall change, not enumerate every commit.
