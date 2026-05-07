Review the design spec.

**Phase 1 — Read.** Read the design spec.

**Phase 2 — Decide.** Pick `approved`, `needs-revision`, or `rejected`.

**Phase 3 — Write the verdict.** Use the Write tool to write the output JSON to the path named in the `## Outputs` section. Three required fields:

- `verdict` — `approved` | `needs-revision` | `rejected`.
- `summary` — 1-3 sentence reason.
- `document` — full review as markdown (what you reviewed, what you noted, why this verdict). Do not end the turn until you have called Write.
