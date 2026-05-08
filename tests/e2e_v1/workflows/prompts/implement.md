Implement the next step from the implementation plan.

You have been given a working git worktree and a stage branch. Make the code changes for **one** plan step:
- Identify which step this is by checking the plan; on iteration `k`, work on step `k` (zero-indexed).
- Edit the files the plan names. Verify each file exists before editing — if a file in the plan is missing or has a different path, locate the real one and use it.
- Run any verification command the plan lists (tests, type-check, lint) before declaring the step complete.
- Stage and commit your changes on the current branch with a descriptive message. The engine will push and open a PR after you exit; do **not** run `git push` or `gh pr create` yourself.

If the plan turns out to be wrong — a referenced symbol does not exist, a step is impossible without changing earlier work, a verification command fails for an unrelated reason — stop and explain in your final response. Do not silently exit without committing; the engine treats "no commits" as failure with no diagnostic.
