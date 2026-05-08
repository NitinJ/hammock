Review the supplied implementation specification.

**Phase 1 — Read.** Read the impl spec carefully against the bug report and design spec it derives from. Verify referenced files / symbols exist (use Grep / Read where useful).

**Phase 2 — Decide.** Pick a verdict:
- **approved** when the spec lists concrete files and symbols that exist in the codebase, scoping changes precisely enough that an implementer could draft a PR from it.
- **needs-revision** when the spec is too abstract to act on, references entities that do not exist, or skips a class of change the bug report demands (tests, migrations, error handling).
- **rejected** when the approach is fundamentally wrong.

**Phase 3 — Write the verdict.** Use the Write tool to write the output JSON to the path named in the `## Outputs` section. Three required fields:

- `verdict` — one of `approved` | `needs-revision` | `rejected`.
- `summary` — 1-3 sentence reason. Specific, not generic.
- `document` — full review as markdown. The dashboard renders this as the primary view; the next iteration's writer agent reads it directly. Cover: what you reviewed, what you noted (concerns, strengths, missing files/symbols, scope issues), and why you reached this verdict. Do not end the turn until you have called Write. The job fails if the output file is missing.
