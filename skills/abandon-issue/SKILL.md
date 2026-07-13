---
name: abandon-issue
description: Abandon an active issue or release a stale issue claim so another agent can pick it up.
---

# Abandon an Issue

Release the current worktree's issue claim. Use this when you determine an
active issue isn't worth fixing, it is blocked on something you can't resolve,
you need to bail out, or a stale claim is preventing new work.

Releasing a stale claim is always authorized and does not require user
approval. A claim is stale when the prior work is demonstrably finished or no
longer active, such as after its resolving PR was merged, or when the worktree
is clean at the default branch with no open PR after completing that work.

## Workflow

1. Run the abandon helper:

   ```bash
   issue-abandon
   ```

   - **Exit 0**: Claim released. The issue is now available for another worktree to claim.
   - **Exit 1**: No active claim found — nothing to abandon.

2. **For abandoned active work, clean up the branch** — reset back to the
   default branch so the worktree is ready for the next issue:

   ```bash
   git checkout $(gh repo view --json defaultBranchRef --jq .defaultBranchRef.name)
   ```

   Skip branch cleanup when merely releasing a stale claim and the worktree is
   already clean and ready. Stale-claim recovery is claim-store bookkeeping;
   do not reset or switch an already-ready branch.

3. If you pushed a remote branch or created a draft PR, consider cleaning those up too (ask the user first).

4. Optionally restart `/solve-issue` to pick a different issue.
