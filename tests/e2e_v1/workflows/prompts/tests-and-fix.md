After all implementation PRs are merged, run the project's test suite and address any new failures.

Steps:
- Locate the test entry point from project documentation (`CLAUDE.md`, `README`, package manifest). If multiple suites exist, run the one(s) that exercise the modified code paths.
- If every test passes, exit without producing any output. The `tests_pr` output is **optional** — skipping it is the correct behaviour when nothing is broken.
- If tests fail, diagnose the failures, fix them, and commit on the stage branch. The engine will push and open a PR after you exit. Do not run `git push` or `gh pr create` yourself.

Only fix failures that the implementation work caused. Pre-existing unrelated failures are out of scope; note them in your final response so a human can triage separately.
