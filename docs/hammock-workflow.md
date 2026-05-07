# Hammock Workflow & Prompt Customization

## Background

Hammock today ships a small set of generic workflows (e.g. `fix-bug`) whose
agent prompts are hardcoded as Python strings inside `engine/v1/prompt.py` and
each artifact type's `render_for_producer` / `render_for_consumer`. Two
limitations follow from this:

- **Customization requires a hammock fork.** Teams cannot adapt the agent's
  behaviour to their codebase's conventions without editing engine code.
- **Prompts cannot ground in the codebase.** Agent dispatch does not always
  root the agent in the project's repo, so project-level documentation
  (`CLAUDE.md`, ADRs, conventions) is invisible to the agent. This produced a
  real failure: an `implement` node ran for 42s and exited silently after the
  upstream design plan referenced symbols (`HIGHLIGHTER_COLORS`) that did not
  exist anywhere in the actual codebase. The agent had no instruction to
  verify entities exist before declaring impossibility, and no cwd-rooted
  view of the code to do so.

This document specifies the workflow + prompt customization model that
addresses both.

## Goals

- Per-node agent prompts live as `.md` files, version-controlled in the
  project's repo, edited in the developer's editor.
- Each workflow is a self-contained folder: `workflow.yaml` plus a sibling
  `prompts/` directory with one file per agent node.
- Hammock ships generic workflows; projects can copy any of them into their
  own repo and edit the copy. Bundled and project-local workflows coexist.
- Every agent node — artifact or code — runs with cwd inside the project's
  repo, so `CLAUDE.md` and other in-repo documentation are loaded for free.
- Narrative artifacts carry a `document: str` markdown field alongside their
  typed fields; the dashboard renders the document as the primary view.
- Prompt assembly is layered: an engine-controlled header and footer wrap a
  customizable middle. The contract (where to write, what shape) stays with
  the engine; the *intent* (what to do, in what style) is the author's.

## Non-goals (v1)

- In-dashboard prompt editing. Editing happens in the user's IDE.
- Project-local artifact types. The type registry stays universal in
  `shared/v1/types/`. New types still require forking hammock.
- Inheritance, overlay, or merging of bundled workflows with project-local
  edits. Copy is a one-way fork.
- Drift detection or re-sync between a project's copy and the current bundled
  workflow.
- Variable substitution (`{{ inputs.bug_report.summary }}`) inside middle
  prompts. The header inlines all input values; the middle is plain text.

## File layout

### Bundled (ships with hammock)

```
hammock/templates/workflows/
└── fix-bug/
    ├── workflow.yaml
    └── prompts/
        ├── write-bug-report.md
        ├── write-design-spec.md
        ├── implement.md
        └── …
```

### Project-local (in the user's repo)

```
<repo>/.hammock/
└── workflows/
    └── fix-bug-highlighter/
        ├── workflow.yaml
        └── prompts/
            └── …
```

The folder name **is** the workflow name. The bundled `fix-bug` workflow and
the project's `fix-bug-highlighter` workflow are two distinct, separately
runnable workflows. Renaming the folder renames the workflow. There is no
`name:` field at the top of `workflow.yaml`; folder name is the source of
truth.

## Discovery and resolution

- **Bundled workflows** are scanned at engine startup from
  `hammock/templates/workflows/`.
- **Project-local workflows** are scanned at project register / verify time
  from `<repo>/.hammock/workflows/`. Re-scanning happens on every project
  re-verify (manual or after the user pushes new commits to `.hammock/`).
- The dashboard's per-project workflow dropdown shows the union: bundled
  workflows + this project's local workflows.
- No merging, no overlay. If a project's folder has the exact name `fix-bug`
  matching a bundled workflow, project-local wins for that project and the
  bundled is hidden. The default copy operation suffixes the folder name to
  avoid this collision.

## Copy-to-project operation

Forking a bundled workflow into a project is a recursive directory copy:

- **Source**: `hammock/templates/workflows/<name>/`
- **Destination**: `<repo>/.hammock/workflows/<name>-<project_slug>/`
- **API**: `POST /api/projects/<slug>/workflows/copy` with `{ source: "<name>" }`
- **Returns**: destination path, suitable for the UI to display as
  "Copy created at `<repo>/.hammock/workflows/fix-bug-highlighter/`."

After copy, the user is responsible for committing the new files via their
normal git flow. Hammock does not run `git add` or `git commit`.

## Prompt assembly

Every agent-actor node's prompt is built at dispatch time in three layers,
concatenated as: **header → middle → footer**.

### Header (engine-controlled, immutable)

- Identity line: workflow name, node id, job slug, attempt N of M.
- For retries (attempt > 1): the previous attempt's failure message verbatim,
  with a leading instruction: *"This is a retry. The previous attempt failed
  for the reason above. Address it directly."*
- Working directory: the absolute path to the cwd, with a note that the
  agent can Read / grep any file in this tree.
- Inputs section. For each input slot:
  - Type name and structured fields rendered as a small bullet list.
  - If the type carries a `document: str` field, the markdown body is
    inlined under a `## Input: <type>` heading.
  - Path to the underlying envelope JSON, with the note that the agent can
    re-read it if needed.
- For code nodes only: the stage branch name, the job branch name, and the
  rule *"commit your changes on the stage branch; do not push and do not run
  `gh pr create`."*

### Middle (workflow author / project author)

- Free-form markdown loaded from `<workflow_dir>/prompts/<node_id>.md`.
- Plain text. No variable substitution. The header has already inlined every
  input value, so the middle can reference them by name in prose.
- The default middle for the bundled `fix-bug` workflow is intentionally
  short, e.g. `prompts/write-design-spec.md`:
  *"Generate a design spec for fixing the bug described in the bug-report.
  Verify that any code entities you reference (functions, constants, files)
  actually exist in this codebase before naming them."*
- A project's customized middle can be much longer and reference local
  conventions, ADRs, style rules, etc.
- Required for every agent-actor node. Missing file = workflow verification
  failure (see below).

### Footer (engine-controlled, immutable)

- Output contract. For each output slot:
  - Target path under `<job_dir>/variables/`.
  - JSON shape derived from the type's Pydantic schema, including the
    `document` field where applicable, with the instruction *"place the
    narrative content in `document` as markdown."*
- Failure-handling rule: *"If you cannot complete this node — because the
  inputs are inconsistent with the codebase, because a referenced entity is
  missing, or for any other reason — produce an output that explicitly says
  so in the `document` field rather than exiting silently."*
- For code nodes only: post-conditions (the engine checks the stage branch
  has commits beyond the job branch).

## The `document` field

Narrative artifact types carry a `document: str` field of markdown alongside
their structured fields:

- Types that get `document`: `bug-report`, `design-spec`, `impl-spec`,
  `impl-plan`, long-form review verdicts, release notes, and any future
  type whose primary content is prose.
- Types that don't: `pr` (no narrative — just branch + commit metadata),
  `job-request` (raw user input), short-form enums and tags.

The wire format remains JSON. The agent writes a JSON file at the same path
as today; one of the fields happens to be a markdown string. Pydantic
validates the envelope as before. Downstream `$var.field` access still works
for every typed field.

### UI rendering contract

If an envelope contains a `document: str` field, the dashboard renders it as
the primary view (markdown). All other typed fields render in a collapsible
metadata panel beside or below the document. This contract is uniform across
every type with `document`; no per-type UI components.

If an envelope has no `document` field, the dashboard falls back to the
existing JSON-dump view.

## Working directory

Every agent node runs with cwd inside the project's repo clone:

- **Code nodes**: cwd is the stage worktree at
  `<job_dir>/repo-worktrees/<node_id>/`, on branch
  `hammock/stages/<slug>/<node_id>` branched off the job branch.
- **Artifact nodes**: cwd is `<job_dir>/repo` directly, on the job branch
  `hammock/jobs/<slug>`. Artifact nodes do not write code, so they do not
  need an isolated worktree; multiple artifact nodes can run from the shared
  clone safely as read-only consumers.

The job branch is the **integration branch** for the workflow run. Stage
branches are merged into the job branch when the human reviewer approves and
merges the stage's PR — this is what lets a downstream `implement` node mark
itself finished, and it is what lets a late-workflow artifact node (say, a
"write release notes" step) see the cumulative state of every merged stage.

This rule is what makes the agent's auto-loaded `CLAUDE.md` story work: the
agent runs in the user's repo, so the user's repo conventions are visible
to it without any hammock-specific configuration.

## Schema versioning

Every `workflow.yaml` carries a top-level `schema_version: 1` field from day
zero.

- The engine refuses to load a workflow whose `schema_version` is unknown or
  greater than the currently supported version.
- The error names the file path and both versions: *"workflow at
  `.../fix-bug-highlighter/workflow.yaml` has schema_version: 2; this
  hammock supports up to 1. Upgrade hammock or roll back the workflow."*
- When the schema evolves, hammock ships migration notes. Users either
  re-copy the latest bundled workflow or hand-upgrade their copy.
- Bundled workflows always ship at the latest supported version.

The version field is mandatory in v1 specifically because retrofitting
versioning into existing workflow files later is significantly harder than
starting with it.

## Workflow verification

When a project is registered or re-verified, hammock runs a verification
pass over every project-local workflow:

- Parse `workflow.yaml`; reject if `schema_version` is missing or unsupported.
- For every agent-actor node, check that
  `<workflow_dir>/prompts/<node_id>.md` exists and is non-empty.
- Validate node ids, input/output references, type names against the
  registered type set.
- Surface results in the project health panel: green badge for healthy
  workflows, red badge with the specific error for invalid ones.

Verification is the chokepoint: a workflow that fails verification cannot
be selected at job submit time. This catches missing prompt files,
schema-version mismatches, and yaml errors before they reach a running job.

## Customization ceiling

Users **can** customize:

- The middle prompt for any agent node, by editing
  `<repo>/.hammock/workflows/<name>/prompts/<node_id>.md`.
- The structure of the workflow itself: which nodes exist, their kinds,
  their dependencies, which types they read and write — by editing
  `workflow.yaml`.
- The display name and dropdown ordering of their custom workflows (folder
  name + an optional `display_name` in `workflow.yaml`).

Users **cannot** customize in v1:

- The set of artifact types. `bug-report`, `design-spec`, etc. are Python
  classes; adding a type or a field on a type still requires forking
  hammock.
- The header or footer of any prompt. The engine owns the contract.
- Engine-actor or human-actor nodes' presentation. They have no agent
  prompt; they have presentation schemas defined per type.

If a project needs to record extra structured information (e.g. a `severity`
field on bug-reports), the v1 answer is to surface it in the markdown
`document` body. Project-local types may be considered in a later version.

## UI surface

### Project detail page

A new "Workflows" section lists every workflow applicable to this project:
the bundled set plus the project's local set, each tagged with its origin
(Bundled / Custom).

Each row shows: workflow name, origin, node count, and an expander.

Expanded view shows each agent-actor node with:

- Node id and kind.
- Prompt source path (absolute, copy-to-clipboard).
- A read-only preview of the current prompt content. For bundled workflows
  the path is inside the hammock install; for custom workflows it is inside
  the project repo.

A "Copy to project" button appears next to each bundled workflow that has
no project-local counterpart. Clicking it calls the copy API and shows a
toast with the destination path.

### Job submit form

The workflow dropdown is unchanged in mechanics. Its contents are now the
union of bundled and project-local workflows, with project-local entries
labelled.

### Verification feedback

The project health panel surfaces workflow verification errors. A workflow
with errors is hidden from the submit dropdown until the errors are fixed.

## Job-time prompt resolution

At node dispatch, the engine:

1. Looks up the workflow that the running job was submitted against.
2. For the agent-actor node about to run, reads
   `<workflow_dir>/prompts/<node_id>.md` from disk.
3. Builds the header from the job and node state (identity, retry context,
   inputs, cwd, branches if applicable).
4. Builds the footer from the node's output slots and their types.
5. Concatenates header + middle + footer.
6. Writes the assembled prompt to
   `<job_dir>/nodes/<node_id>/runs/<n>/prompt.md` (this already happens
   today; the change is the source of the middle).
7. Spawns `claude -p <prompt>` with the cwd dictated by the working-directory
   rule.

If the prompt file is missing at dispatch (i.e. it slipped past
verification), the node fails fast with a clear error before claude is
invoked.

## Stage-by-stage implementation plan

The work ships in six stages. Each stage is one PR and follows
**red → green → refactor**:

- **Red** — write or update integration tests first. The tests describe
  the contract this stage delivers. They must run and fail for the right
  reason before any implementation begins.
- **Green** — implement the smallest change that turns the new tests green
  while keeping every previously-green test green. Tests are **frozen**
  during this phase: implementation must conform to the tests, not the
  other way around. If a test turns out to be wrong, finish the stage,
  then file a follow-up — do not edit the test mid-flight.
- **Refactor** — clean up dead code (e.g. the Python prompt strings
  superseded by `.md` files) once the new path is proven green.

### T1–T6 invariant

The existing real-claude end-to-end suite (`T1` through `T6`, exercising
the bundled `fix-bug` workflow) must remain green after every stage. Each
stage's "T1–T6 contract" subsection states the specific risk for that
stage and what the implementer must verify before opening the PR:

- Run the full T1–T6 suite locally before pushing.
- If a stage's test changes affect what T1–T6 assert (e.g. envelopes now
  carry `document`), the T1–T6 fixtures and assertions are updated **in
  the red phase of the same stage**, never retroactively in a later one.

### Stage 1 — Externalize bundled prompts

**Goal.** Move the agent-prompt middle text out of Python and into
per-node `.md` files shipped under
`hammock/templates/workflows/fix-bug/prompts/`. No behavioural change for
the user; this is the foundation every later stage builds on.

**Red.**
- New integration test: for each agent-actor node in the bundled `fix-bug`
  workflow, assert that
  `hammock/templates/workflows/fix-bug/prompts/<node_id>.md` exists and is
  non-empty.
- New integration test: dispatch a single artifact node through the engine
  with a stub claude runner that captures the assembled prompt; assert
  the assembled prompt contains a substring drawn from the new `.md`
  file. Repeat for one code node.
- Update existing `engine/v1/prompt.py` unit tests to reflect the
  header/middle/footer assembly contract.

**Green.**
- Create `prompts/<node_id>.md` files for every agent-actor node in
  `fix-bug`, with content carried over from the current Python prompt
  strings.
- In `engine/v1/prompt.py` and `engine/v1/code_dispatch.py`, read the
  middle text from the workflow's `prompts/` directory at dispatch time.
  Header and footer assembly stay in Python.
- The bundled `fix-bug` workflow yaml is untouched at this stage.

**Refactor.** Delete the now-superseded Python prompt strings.

**T1–T6 contract.** Real-claude prompts must remain semantically
equivalent. Risk: subtle whitespace or wording changes during the
extraction shift agent behaviour. Mitigation: extract by copy-paste, run
T1–T6 locally, and only fix-up wording in a separate later stage if
needed.

### Stage 2 — `document` field on narrative artifact types

**Goal.** Every narrative artifact type carries a `document: str` field
of markdown alongside its typed fields. The dashboard renders the
document as the primary view.

**Red.**
- Update unit tests for each narrative type (`bug-report`,
  `design-spec`, `impl-spec`, `impl-plan`, others identified during the
  stage) to assert the `document` field is required.
- Update T1–T6 e2e tests: assert that produced envelopes contain a
  non-empty `document` field with markdown content.
- New frontend Vitest test for `NodeDetail.vue`: when the envelope has
  `document`, render markdown; when absent, render the existing JSON
  view.
- New Playwright test: open a node detail page for a node whose envelope
  has `document`; assert the markdown renders.

**Green.**
- Add `document: str` to each narrative type's Pydantic model. Update the
  type's `render_for_producer` footer instruction to mention `document`
  and require markdown content. Update the type's `render_for_consumer`
  to inline the document under a `## Input: <type>` heading.
- Update the bundled `fix-bug` prompt `.md` files (Stage 1 outputs) to
  reflect the new contract — every agent producing a narrative type must
  be told to fill `document`.
- Frontend: extend `NodeDetail.vue` with a markdown-rendered primary view
  when `document` is present, and a collapsible metadata panel for the
  remaining fields.

**Refactor.** None expected.

**T1–T6 contract.** Tests now require `document` in produced envelopes.
Update T1–T6 fixtures and assertions in the red phase of this stage.
Risk: real claude may take a run or two to produce well-formed
documents; this is a real-claude tuning concern, not a hammock bug, and
should be fixed by tightening the prompt `.md` files (Stage 1 artifacts).

### Stage 3 — Working-directory rule

**Goal.** Every agent node — artifact and code — runs with cwd inside
`<job_dir>/repo`. Artifact nodes share the job-branch checkout directly;
code nodes get a stage worktree as today.

**Red.**
- New integration test: dispatch an artifact node and assert the spawned
  claude process was invoked with `cwd = <job_dir>/repo`.
- New integration test: drop a sentinel file into the project repo
  before dispatch; assert the artifact agent's prompt assembly references
  the cwd path that contains the sentinel; with the stub runner, assert
  cwd is set such that a `Read` of the sentinel would resolve.
- Update existing artifact-dispatch tests that asserted any other cwd.

**Green.**
- Modify `engine/v1/artifact.py`'s subprocess spawn to set cwd =
  `<job_dir>/repo`. The substrate's `copy_local_repo` already runs at
  job submit, so the clone exists.
- Confirm the job branch (`hammock/jobs/<slug>`) is checked out in
  `<job_dir>/repo` at the time of every artifact dispatch — no
  per-dispatch checkout logic, but assert the invariant in the test.

**Refactor.** None expected.

**T1–T6 contract.** Artifact agents now have access to project files via
`Read`. They should produce *better* outputs, not different shapes.
Verify T1–T6 still produce envelopes with the same typed fields.

### Stage 4 — Workflow `schema_version`

**Goal.** Every `workflow.yaml` carries `schema_version: 1`. The engine
rejects unknown or missing versions with a clear error.

**Red.**
- Update the workflow-loader tests: a yaml without `schema_version` must
  be rejected; a yaml with `schema_version: 999` must be rejected; a
  yaml with `schema_version: 1` loads.
- Update fixture workflows (including the bundled `fix-bug.yaml` and any
  test fixtures under `tests/`) to include `schema_version: 1`.

**Green.**
- Add the field to the workflow Pydantic schema in `shared/v1/workflow.py`
  with validation that the value is `1`.
- Surface a clear error in the workflow loader.
- Update bundled and test workflow yamls.

**Refactor.** None expected.

**T1–T6 contract.** Mechanical fixture update. T1–T6 use the bundled
`fix-bug.yaml`, which gains the field.

### Stage 5 — Project-local workflow discovery

**Goal.** When a project is registered or re-verified, hammock scans
`<repo>/.hammock/workflows/` and surfaces every valid project-local
workflow alongside the bundled set in the dashboard.

**Red.**
- New integration test (`tests/integration/dashboard/test_projects.py`
  or sibling): create a temp project with
  `.hammock/workflows/foo/workflow.yaml` and
  `.hammock/workflows/foo/prompts/<node>.md`; register the project;
  assert `GET /api/projects/<slug>` lists `foo` as a project-local
  workflow.
- New integration test: a project-local workflow missing a required
  prompt file or with an unsupported `schema_version` surfaces as a
  verification error on the project record and is excluded from the
  selectable set.
- New integration test: submit a job against a project-local workflow;
  assert the engine reads middle prompts from the project's
  `<repo>/.hammock/workflows/<name>/prompts/` directory, not from the
  bundled location.
- Update workflow-listing API tests to expect the union shape (bundled +
  project-local).

**Green.**
- Extend `dashboard/api/projects.py` verify path to scan
  `.hammock/workflows/` and validate each subfolder.
- Extend the project record schema with a `workflows: list[…]` field
  carrying name, source (bundled / custom), validation status.
- Extend the workflow-listing endpoint to merge bundled and
  project-local sets per project.
- Extend `engine/v1/prompt.py` (and `code_dispatch.py`) to resolve the
  prompts directory from the workflow's source path, which is already
  carried on the job record.

**Refactor.** None expected.

**T1–T6 contract.** T1–T6 use bundled workflows only; project-local
discovery is purely additive. Verify the bundled set still loads.

### Stage 6 — Copy API and project-page UI

**Goal.** A "Copy to project" button on each bundled workflow forks it
into the project's repo as `.hammock/workflows/<name>-<project_slug>/`.

**Red.**
- New API integration test: `POST /api/projects/<slug>/workflows/copy`
  with `{ source: "fix-bug" }` creates the destination folder with
  `workflow.yaml` and the full `prompts/` subtree, and returns the
  destination path. Calling it twice returns a 409 conflict.
- New Playwright test: on the project detail page, the Workflows section
  lists the bundled `fix-bug`; clicking "Copy to project" creates the
  custom workflow and the new entry shows up in the dropdown on the
  job-submit form.
- New frontend Vitest test for the new component(s) (workflow list +
  per-node prompt preview).

**Green.**
- Implement the copy endpoint in `dashboard/api/projects.py` (or a new
  `dashboard/api/workflows.py`) as a recursive directory copy.
- Add the Workflows section to `ProjectDetail.vue`, the per-workflow
  expander with prompt previews, and the copy mutation in
  `dashboard/frontend/src/api/queries.ts`.
- Add markdown preview rendering for prompt files (read-only).

**Refactor.** None expected.

**T1–T6 contract.** Purely additive surface. T1–T6 unaffected.

### Stage ordering rationale

The order is dictated by dependencies, not by where the user-visible
payoff lives:

- Stage 1 (prompts as files) is a precondition for Stage 6 (copy
  operation copies a folder) and Stage 5 (project-local workflows are
  folders).
- Stage 2 (`document` field) depends on Stage 1's footer-instruction
  surface to tell the agent to fill `document`.
- Stage 3 (cwd rule) is independent of Stages 1, 2, 4 in code, but is
  best landed before Stage 5 so that "what does customization do for
  me?" answers from day one include "your `CLAUDE.md` is loaded."
- Stage 4 (`schema_version`) is mechanical; landing it before Stage 5
  means project-local workflows are validated against versioning from
  the moment they exist.
- Stage 5 must precede Stage 6: the discovery and resolution path is
  what the copy operation populates.

## Out of scope for v1

- In-dashboard prompt editor.
- Live diff between a project's copy and the current bundled version.
- Re-sync / merge upstream bundled changes into a custom copy.
- Variable substitution / templating in middle prompts.
- Project-local artifact types.
- Per-project header or footer overrides.
- Sharing custom workflows across multiple projects.
