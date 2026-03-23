---
name: abandon-issue
description: Abandon the currently claimed issue, releasing the local claim so another agent can pick it up.
---

# Abandon an Issue

Release the current worktree's issue claim without completing it. Use this when you determine an issue isn't worth fixing, is blocked on something you can't resolve, or you need to bail out for any reason.

## Workflow

1. Run the abandon helper:

   ```bash
   issue-abandon
   ```

   - **Exit 0**: Claim released. The issue is now available for another worktree to claim.
   - **Exit 1**: No active claim found — nothing to abandon.

2. **Clean up the branch** — reset back to the default branch so the worktree is ready for the next issue:

   ```bash
   git checkout $(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
   ```

3. If you pushed a remote branch or created a draft PR, consider cleaning those up too (ask the user first).

4. Optionally restart `/solve-issue` to pick a different issue.
