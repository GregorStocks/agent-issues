---
name: solve-issue
description: Claim exactly one issue, fix it, and create a pull request starting from a clean branch.
---

# Solve an Issue

Pick and solve exactly **one** issue, then create a PR. Issue claims live in the shared local claim store under the repo's git common dir; claiming does **not** create a PR up front.

## Repo-Specific Instructions

**Before starting**, check for a repo-scoped skill with additional instructions:
- `.claude/skills/solve-issue-local/SKILL.md` (Claude Code)
- `.agents/skills/solve-issue-local/SKILL.md` (Codex)

If either exists, read it and follow its instructions alongside this workflow. The local skill defines repo-specific build commands, test commands, lint targets, and any special considerations.

## Workflow

0. **Preflight check** — run this single command to verify the working tree is clean, HEAD matches `origin/<default-branch>`, and no open PR is already tied to the current branch:

   ```bash
   agent-preflight
   ```

   If it exits non-zero, **stop immediately** and tell the user what failed. Do not proceed — solve-issue must start from a clean branch that matches the default branch exactly and is not already tied to an open PR.

1. **Resolve a user-supplied issue argument** — only if the user explicitly passed an issue name/path. Use your judgment to determine the issue file they very obviously meant before invoking the claim script.

   Canonicalize the argument to the basename expected by `issue-autoclaim`:
   - Issue filenames are prefixed `p1-...`, `p2-...`, `p3-...`, `p4-...`, or `blocked-...`
   - If they passed `issues/<name>.json5`, strip the leading `issues/`
   - If they passed `<name>` without `.json5`, try `<name>.json5`
   - If they passed a path or near-exact basename that uniquely identifies one file under `issues/`, use that file's basename

   Do **not** silently switch to a different issue or auto-pick a replacement. If there is no single obvious match, **stop immediately** and ask the user to clarify instead of guessing.

2. **Try to claim an unblocked issue first** by running:

   ```bash
   issue-autoclaim
   ```

   This auto-picks the highest-priority unclaimed issue, skipping issues with a truthy `blocked` field (those have preconditions that need manual review). The `blocked` field can be `true` or a string describing the blocker.

   **Only if the user explicitly passed an issue name** (e.g. `/solve-issue populate-deck-strategies` or `/solve-issue issues/populate-deck-strategies.json5`), claim that resolved canonical issue instead:

   ```bash
   issue-autoclaim <resolved-issue-name>
   ```

   Never pick a specific issue on your own — always use the auto-pick unless the user told you which issue to work on.

   - If the script **succeeds** (exit 0): immediately run `issue-claim --current` and treat the returned filename stem as the authoritative claimed issue for all later steps.
   - If you later merge the default branch and the claimed issue file was renamed (for example because issue filename prefixes changed), re-run `issue-claim --current` before continuing. The local claim key is stable across `blocked-...` / `pN-...` renames.
   - If the script **fails with exit 2**: **stop immediately**. Tell the user no issue was claimed and do NOT proceed.
   - If the script **fails with exit 1** and the user explicitly passed an issue name: **stop immediately**. Tell the user no issue was claimed and do NOT proceed.
   - If the script **fails with exit 1** during auto-pick: do **not** stop yet. This means there is no unblocked, unclaimed issue currently available. Continue to step 3 and look for a blocked issue that can be unblocked and claimed.

3. **Fallback to blocked issues only if auto-claim found nothing.** Skip this step if the user explicitly passed an issue name or step 2 already claimed something.

   `issue-query` is still useful for listing blocked issues, but do **not** use it to decide whether blocked fallback is needed. It does not know which unblocked issues are already claimed in the shared local claim store; `issue-autoclaim` is the authoritative check for "nothing unblocked is actually claimable."

   Work through blocked issues in any reasonable order. Prefer blockers you can verify mechanically before issues that obviously require the user or an external dependency.

   1. Read the blocked issue's JSON5 file — the `blocked` field is a string describing why it's blocked
   2. Investigate whether the blocker has been resolved: check the codebase, git history, external conditions described in the blocker string
   3. If the blocker **IS resolved**: first try to claim that specific blocked issue as it exists on disk:

      ```bash
      issue-autoclaim <blocked-issue-name>
      ```

      - If that claim succeeds (exit 0): run `issue-claim --current` and treat that returned filename stem as authoritative for all later steps. Then immediately remove the `blocked` field, rename the file from `blocked-<name>.json5` to `p{priority}-<name>.json5`, commit that change on your branch, and continue. Stop scanning blocked issues.
      - If that claim fails with exit 1: another worktree got there first or the claim was otherwise lost. Continue to the next blocked issue and try again with a different one.
      - If that claim fails with exit 2: **stop immediately** and tell the user.
   4. If the blocker **is NOT resolved**: leave it blocked and continue to the next blocked issue
   5. If no blocked issue can be unblocked and claimed, **stop immediately** and tell the user no issue was claimed.

4. **Check if already fixed** — before planning anything, check whether the issue was already resolved and the issue file just wasn't cleaned up. Do this by:
   - Finding when the authoritative claimed issue file was created (`git log --diff-filter=A -- issues/<filename>.json5`)
   - Reviewing git history since that date for commits that look like they address the issue
   - Reading the relevant code to see if the described bug/problem still exists

   If the issue **is already fixed**: skip the planning/implementation steps entirely. Just delete the issue file, commit it, push, and finalize the PR as a cleanup. The PR title should be something like "Clean up outdated issue: \<title\>" and the body should briefly explain that the issue was already resolved (mention the commit or change that fixed it). Conceptually this is a zero-line fix — the only change is removing the stale issue file.

   If the issue **is NOT fixed**: continue to step 5.

5. **Enter plan mode** — explore the codebase, design your approach, and present it to the user for feedback before writing any code. This is the user's chance to redirect you if the approach is wrong.

   Start the plan with a short **issue context** recap in plain language: what the bug/task actually is, how it shows up today, and why the proposed fix addresses it. Do not assume the user remembers the issue details from when they filed it.

   **Your plan must end with this checklist** (substitute repo-specific build/test/lint commands from the local skill if available):

   ```markdown
   ## Post-implementation checklist
   - [ ] Implement the changes described above
   - [ ] Add/update tests
   - [ ] Run lint/typecheck/tests (use repo-specific commands from solve-issue-local)
   - [ ] Delete the issue file and include deletion in the commit
   - [ ] Review changed code for quality
   - [ ] Submit PR: `agent-submit --title "..." --body "..."` (handles push, PR create/update, and CI watcher)
   ```

   This checklist survives the plan mode boundary and ensures no steps are skipped even if earlier context is compressed.

6. After the plan is approved, **create tasks** from the checklist using `TaskCreate`. Mark each task in_progress when you start it and completed when you finish it.
   - If `TaskCreate` is unavailable in the current session, mirror the checklist in `update_plan` instead and keep the statuses current there.

7. Implement the fix. Push progress:

   ```bash
   git push origin HEAD
   ```

8. Update tests to expect the correct behavior.

9. Run the repo's lint/typecheck/test suite. Consult the local `solve-issue-local` skill for the specific commands. If no local skill exists, look for `Makefile` targets like `make check`, `make test`, or `make lint`.

10. Delete the issue file (e.g., `rm issues/<issue-filename>.json5`) and **include the deletion in the commit** — the issue removal must ship with the fix.

    - If you merged the default branch after claiming, re-check whether the issue file was renamed (for example to add a priority prefix or `blocked-` prefix) and delete the renamed path that now exists on your branch. If `issue-claim --current` can no longer resolve the claim because the file is gone, that does not mean the claim itself is gone — `agent-submit` does not read the claim file.

11. **Document ALL issues you discover** during exploration, even if you're only fixing one. Future agents benefit from this documentation! Document them by filing new issues in issues/.

12. Review the changed code for reuse, quality, and efficiency. Fix any issues found. If the repo has a `/simplify` skill, use it.

13. Push final changes and finalize the PR. Run `agent-submit` — it pushes, creates or updates the branch PR, and runs the CI watcher end-to-end:

    ```bash
    agent-submit --title "<concise PR title>" --body "<PR description with summary, test plan>"
    ```

    The PR body must include a short **issue context** section near the top that explains what the original issue was and why this change fixes it. Write it for a reader who may not remember the issue they filed days earlier.

## Abandoning an Issue

If you determine an issue isn't worth fixing after claiming it, run:

   ```bash
   issue-abandon
   ```

   This releases the local claim so another worktree can pick up the issue. See `/abandon-issue` for the full workflow including branch cleanup.

Then restart from step 1 to pick a different issue.

## Is It Worth Fixing?

Not every quirk deserves a fix. For issues that seem one-in-a-million or where it's not realistically possible to determine the original author's intent, it's fine to give up and handle it gracefully. Being correct on fewer things is better than being _wrong_.

## Important

- One issue per PR — keeps PRs small and reviewable
- Don't chain multiple issues — after CI is green and feedback is addressed, stop
- Never switch branches — work on whatever branch you're already on. The caller is responsible for putting you on the right branch before invoking this skill.
