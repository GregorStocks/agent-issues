---
name: solve-issue-eager
description: Like solve-issue, but skips plan confirmation and goes straight to implementation.
---

# Solve an Issue (Eager)

Follow the full `/solve-issue` skill workflow with one change:

**Skip step 5 (plan mode).** Do not enter plan mode or wait for user approval. After checking whether the issue is already fixed (step 4), go straight to implementation (step 6 onward).

The user will give feedback on the resulting PR instead.
