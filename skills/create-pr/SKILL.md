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

If either exists, read it and follow its instructions alongside this workflow.
The local skill should stay narrow: repo-specific validation commands,
generated-artifact rules, dependency policy, pre-PR checks, and other project
constraints.

Do not expect `create-pr-local` to repeat shared PR mechanics. This skill owns
branch summarization, default-branch merges, validation timing, `submit-pr`
handoff, PR creation or update, CI watching, review feedback loops, and timeout
handling through `submit-pr` and `agent-submit`.

## Workflow

1. **Commit any uncommitted work.** Check `git status` — if there are staged or unstaged changes, commit them before proceeding. Everything that's part of this PR should be in a commit.

2. **Understand the full scope of changes.** Run this single command — it fetches origin, then prints the commits ahead of the default branch and the diff stat:

   ```bash
   branch-summary
   ```

   Read through the actual diffs and changed files — don't just look at filenames. You need to understand what changed and why.

3. **Merge the default branch** so you're testing against the latest code:

   ```bash
   git merge --no-edit origin/$(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
   ```

   Fix any merge conflicts before proceeding. Use `--no-edit` so repo merge settings do not drop you into an interactive editor mid-workflow.

4. **Format issues.** Run `issue-fmt` to auto-format issue files before validation.

5. **Run pre-validation steps** from the local skill if it exists (e.g., regenerate stale test fixtures, build generated code). Skip this step if no local skill is present or if the local skill says those steps are owned by `.agent-issues/submit-hooks.json5`.

6. **Run the validation suite.** Consult the local `create-pr-local` skill for the specific commands. If no local skill exists, look for `Makefile` targets like `make check`, `make test`, or `make lint`. Fix any failures before proceeding. Do not create a PR with failing checks. If repo validation is wired into `.agent-issues/submit-hooks.json5`, `agent-submit` will run it during the next step; do not duplicate long-running checks unless the local skill asks for an earlier targeted run.

   After validation, run `git status` again before pushing. Build and test commands can dirty tracked files. Commit intentional artifacts or clean incidental churn before you open the PR.

7. **Submit the PR.** Invoke the `submit-pr` skill (no arguments) — it composes the title and body from the branch state, pushes, opens or updates the PR, and loops through CI failures and review feedback until the PR is clean.

## Guidelines

- **One logical change per PR** — don't bundle unrelated work.
- If the branch has many commits, the PR description should synthesize the overall change, not enumerate every commit. `submit-pr` handles this.
