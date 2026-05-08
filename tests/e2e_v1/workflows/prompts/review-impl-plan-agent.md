Review the supplied implementation plan.

**Phase 1 — Read.** Read the impl plan carefully against the impl spec it derives from. Trace each plan step back to a spec requirement.

**Phase 2 — Decide.** Pick a verdict:
- **approved** when the plan's steps are individually shippable, ordered correctly, and collectively cover everything the impl spec requires.
- **needs-revision** when steps are too coarse to land as separate PRs, dependencies between steps are wrong, or the plan omits work the spec calls out.
- **rejected** when the staging is fundamentally wrong.

**Phase 3 — Write the verdict.** Use the Write tool to write the output JSON to the path named in the `## Outputs` section. Three required fields:

- `verdict` — one of `approved` | `needs-revision` | `rejected`.
- `summary` — 1-3 sentence reason. Specific, not generic.
- `document` — full review as markdown. The dashboard renders this as the primary view; the next iteration's writer agent reads it directly. Cover: what you reviewed, what you noted (step ordering, missing scope, dependency mistakes), and why you reached this verdict. Do not end the turn until you have called Write. The job fails if the output file is missing.
