Read the bug report and produce a design spec describing how to fix the bug.

The design spec should:
- State the goal of the change in one paragraph.
- Identify the modules, files, and functions involved. **Verify each named entity exists in the codebase** — grep, read, or otherwise confirm before naming it. If something the bug report mentions does not exist by that name, find the actual entity and use its real name; do not invent symbols.
- Describe the proposed approach at the level a reviewer can sanity-check without reading the eventual diff.
- Call out any non-obvious risks, prior workarounds, or test coverage gaps.

If the bug report describes something that cannot be located in the codebase, say so explicitly in the design spec rather than producing a plausible-sounding but ungrounded plan.
