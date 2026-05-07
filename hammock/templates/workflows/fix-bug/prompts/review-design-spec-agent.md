Review the supplied design spec.

**Phase 1 — Read.** Read the design spec carefully. Verify it references real entities in the codebase (use Grep / Read where useful).

**Phase 2 — Decide.** Pick a verdict:
- **approved** when the spec is concrete, grounded in entities that actually exist in the codebase, and would let an implementer proceed without further clarification.
- **needs-revision** when the spec is vague, references non-existent symbols, omits an important risk, or proposes an approach that would not address the bug.
- **rejected** when the approach is fundamentally wrong and revising won't save it.

Approval is the default only when the spec genuinely meets the bar — do not approve out of politeness.

**Phase 3 — Write the verdict.** Use the Write tool to write the output JSON to the path named in the `## Outputs` section. Three required fields:

- `verdict` — one of `approved` | `needs-revision` | `rejected`.
- `summary` — 1-3 sentence reason. Specific, not generic.
- `document` — full review as markdown. This is the primary view the operator sees in the dashboard, and the next iteration's writer agent reads it directly. Cover: what you reviewed, what you noted (concerns, strengths, missing context, references to specific lines/files), and why you reached this verdict. Do not end the turn until you have called Write. The job fails if the output file is missing.
