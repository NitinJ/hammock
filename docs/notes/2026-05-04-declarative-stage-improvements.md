# Declarative stage definitions — improvements TODO

Captured during the real-claude e2e dogfood (PR #29). Ad-hoc prompt-builder
hardcoding accumulated in `job_driver/prompt_builder.py`; the rules below
live there imperatively when they should be properties of the declarative
stage / schema / agent definitions.

## What got hardcoded (and what it should be instead)

### 1. `_SCHEMA_HINTS` dict per schema name
Currently in `prompt_builder.py`: a manual mapping from schema name → field
list / constraints. Adding a new validator schema requires editing
`prompt_builder.py`.

**Better:** each Pydantic model in `shared/models/*.py` exposes a class
attribute or method like `prompt_hint() -> str` (or auto-generated from
`model_json_schema()` with a curated subset). The prompt builder iterates
declared validators, looks up the model from
`shared.artifact_validators.REGISTRY`, and emits the hint without any
prompt-builder edit.

### 2. Branch + PR protocol inline in every prompt
Currently a fixed text block warning every agent that "if your stage
mentions PR/merge/push, you MUST do these 5 steps." That's prompt
boilerplate dependency on stage description wording.

**Better:** add `StageDefinition.produces_pr: bool` (or a more general
`required_capabilities: list[Literal["git_push", "open_pr", "run_tests"]]`).
The prompt builder emits the capability-specific protocol only when the
stage declares it. Templates declare requirements declaratively; the
agent doesn't have to interpret prose.

### 3. JOB DIR vs working directory disambiguation
Currently every prompt explains "outputs go to JOB DIR, code edits go to
the worktree." Repeated boilerplate.

**Better:** make this part of the agent specialist's system prompt
(once-per-agent-class) rather than every per-stage user prompt. Agents
inherit the contract; the per-stage prompt only carries stage-specific
context.

### 4. Tools available list
Currently a fixed enumeration of git/gh/pytest. As more tools land
(playwright, dart, etc.) this would balloon.

**Better:** drive from a `tools_available` declaration on the agent
specialist or project config. The prompt builder enumerates only what
the spawned environment actually carries — no need for the prompt to
list tools the agent can't use.

### 5. is_expander → plan.yaml glue
Currently the JobDriver's `_merge_plan_yaml_into_stage_list` knows the
expander's output filename is `plan.yaml`. Templates can pick a
different name and break the merge.

**Better:** declare the plan-output convention in `StageDefinition`
itself (e.g. `expander_output: str = "plan.yaml"`) so the merge looks
at the declared path, not a hardcoded one. Or even tighter: the
expander's required_outputs[0] is the plan, by convention.

### 6. Insertion point for appended stages
Currently `_merge_plan_yaml_into_stage_list` heuristically inserts after
the last `review-{expander_id}-*` stage. Templates that don't follow
the `review-X-agent / review-X-human` naming convention break this.

**Better:** declare insertion via `StageDefinition.expander_inserts_after:
str | None` (the id of the stage after which appended stages should land)
or use loop-back-style explicit ordering metadata. Right now the heuristic
holds for the bundled templates but is fragile.

### 7. PR / merge artefact contract on stages that produce them
The pr-merger stage's required_output is currently `pr-merge-summary.md`
with `validators: [non-empty]`. There's no schema enforcing the file
contains a PR URL.

**Better:** introduce a `pr-merge-summary-schema` validator that requires
a `pr_url` field. The schema enforcement closes the loop: claude has to
include a real URL or validation rejects the artefact, instead of
relying on outcome-side string-matching for `https://`.

## Other observations from the dogfood

- **stage descriptions vs agent prompts.** Each stage's `description`
  field is short; the heavy lifting is in the agent specialist's system
  prompt (which currently isn't bundled — `agents.json` is empty in
  v0). Once agent overrides ship as part of the project, most of the
  hardcoded rules in `prompt_builder` move to specialist `.md` files
  per-agent-ref, where they belong.
- **plan-schema's recursive shape.** `Plan.stages: list[StageDefinition]`
  with all nested types makes the JSON Schema huge. The prompt-builder
  hint is a hand-curated digest. Auto-generating from
  `model_json_schema()` would be authoritative but verbose; some
  pruning (drop `description` text from sub-schemas, fold optional
  nullable types) is needed for prompt economy.
- **the unified-impl alternative.** Splitting `implement-1` from
  `pr-merge-1` is what the bundled `impl-plan-spec-writer` agent does
  by default. Worth re-evaluating: a single `implement-and-pr` stage
  with a single specialist would avoid plumbing the PR URL across
  artefacts and would keep the work + record together. Trade-off:
  loses failure-isolation between code and push, but for v0 that
  granularity may be over-engineered.
