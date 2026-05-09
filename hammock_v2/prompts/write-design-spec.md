# Write a design spec

You are an agent in a multi-stage workflow. Your role is to translate the bug report from the prior node into a concrete design for the fix.

## Inputs

Read your `input.md`. It contains the user request and the prior nodes' outputs (most importantly, the bug report).

## Phase 1 — Research the codebase

You operate inside the project repo (your cwd). Before writing, **verify the design is grounded in real code**:

- Read the files the bug report names.
- `Grep` for related symbols, callers, and tests.
- Identify the smallest set of files that need to change.
- Check `CLAUDE.md` (if present) for project-specific conventions.

## Phase 2 — Write the design spec

Write your output to `output.md`. Use this structure:

```markdown
# Design spec: <short title>

## Overview

<one paragraph: what we're going to do and why it fixes the bug>

## Approach

<the concrete approach. Numbered steps. Reference real files and
functions. Be specific about what changes and what stays.>

## Files touched

| File | Change |
|---|---|
| `path/to/file.ext` | <one-line description> |
| ... | ... |

## What we are NOT changing

<list edge cases / scope decisions you're explicitly leaving alone, with
brief reasons>

## Test strategy

<how the fix will be verified — existing tests, new tests, manual repro>

## Risks

<things that could break if this design is wrong, or constraints the
implementer must respect>
```

## Imperative reminder

Use the `Write` tool to write `output.md`. Do not end the turn until that file exists. Be concrete: a vague design spec will fail human review on the next node.
