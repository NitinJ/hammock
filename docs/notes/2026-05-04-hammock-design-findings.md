# Hammock design findings — post real-claude e2e dogfood

Captured 2026-05-04, after iterating the real-claude e2e test (`docs/specs/2026-05-03-real-claude-e2e-test-design.md`) across ~16 attempts on branch `feat/real-claude-e2e-test`. The point of this document is not the bug list — that's already in `2026-05-04-declarative-stage-improvements.md`. The point is the architectural shape that produced the bugs, and what Archon (`/home/nitin/workspace/Archon`) does differently.

## 0. Bird's-eye view (executive)

### The pattern behind every bug

We tried to make our test actually run end-to-end. It took 16 attempts. Each attempt fixed one bug and exposed the next. The bugs looked unrelated, but they all came from the same shape:

**Hammock's brain (the executor) keeps too many secrets in prose, and too few in contracts.**

Our agents read prose instructions ("if your stage involves a PR, do these five things"). Our executor reads naming conventions ("if a stage starts with `write-`, its reviewer is named `review-{rest}-`"). Our test reads internal caches that were never meant to be public. Every one of those is a handshake done by reading text — and every one of them broke at some point during the dogfood.

When something is a contract, the system can check it. When something is prose, the system has to hope someone read it correctly.

### What broke, in plain language

1. **Branches were missing.** A function that should have set up the working branch was never called. Nothing crashed — it just silently fell through, so per-stage isolation didn't happen and downstream checks couldn't find what they expected.
2. **Stages ran in the wrong order.** When the executor needed to insert new stages mid-flight, it guessed where to put them by pattern-matching names. The pattern guessed wrong, so the implementation ran *before* the plan was reviewed. The test still passed by luck.
3. **Agents kept inventing fields the schema rejected.** We patched this by hand-writing reminders into the prompt ("don't add fields X, Y, Z"). Every new schema means a new hand-written reminder.
4. **The agent had to be talked into opening a PR.** We injected a five-step prose protocol into the prompt: "if your stage description mentions PR/merge/push, you must do these things." This is correctness held together by string matching.
5. **Skipped stages confused the test.** When a stage was correctly skipped (because tests passed and human review wasn't needed), the test still expected its outputs to exist. We had to teach the test about skipping after the fact.
6. **Cleanup couldn't actually clean up.** Our teardown ran `git push --delete` from the wrong directory, so it talked to the wrong remote. We never noticed because everyone was creating fresh repos every run.
7. **Open PRs and stale branches piled up.** We weren't closing PRs before deleting branches, so cleanup hit a wall. Same root cause — the cleanup contract was never spelled out.
8. **The agent didn't know where to write things.** Stage outputs go in one folder, code edits go in another. We solved this by repeating "remember, JOB_DIR vs working directory" in every prompt. That's tax we pay forever.

### The bottlenecks

These aren't bugs. They're shapes that produce bugs.

- **The executor encodes conventions.** Naming patterns (`write-X` → `review-X-agent`), filename conventions (`plan.yaml`), insertion-point heuristics — these live as if-statements in our brain. Every new template is a new patch.
- **The agent is glued to the system by prose.** The prompt carries the protocol the agent must follow. Change the wording, break the system. Add a feature, write more prose.
- **Stages have no type.** A stage that opens a PR and a stage that writes a JSON review look identical to the executor. So we can't say "this kind of stage must push a branch" — we have to ask the agent to do it via prompt.
- **What "passing" means is implicit.** The test had to be retrofitted multiple times to handle skipped stages, conditional flow, optional outputs. There's no single contract that says "these stages must exist, these must succeed, these may be skipped."
- **Tests reach into internal state.** Our HIL stitching had to call a private cache function to make items visible. That's not testing — that's the test bypassing the system.

### What Archon does differently (and what we can borrow)

Archon (similar problem space, more mature contracts) shows five patterns worth taking:

1. **Stages have a kind.** Archon distinguishes between a "run a command" node, a "prompt an LLM" node, an "approval" node, a "branch/loop" node. The engine knows what kind it's dealing with and runs the right machinery. Ours: every stage is the same shape, so all the differentiation lives in conventions.
2. **Stages reference each other by name.** Archon stages declare "I depend on the output of node X" rather than "I run after node X in this list." This makes ordering structural, not positional. Insertion-point bugs disappear.
3. **Prompt hints come from the schema.** Archon doesn't maintain a hand-written cheat sheet of allowed fields — the same schema that validates the output also generates the prompt hint. One source of truth. Add a field, the hint updates automatically.
4. **Approvals and retries are first-class.** Archon's approval gates aren't conventions; they're built into the engine. The agent doesn't read prose telling it to wait — the engine waits. Ours: every approval is a stage that follows a naming pattern.
5. **Identities are typed.** A branch isn't a string in Archon — it's a typed value that knows which repo it belongs to. The "git push to the wrong remote" class of bug is impossible by construction.

### What I recommend, in order

This is sequenced low-risk-first. Each step makes the next safer.

1. **Stop relying on naming patterns.** Add explicit metadata to stage definitions: "this is the expander", "insert appended stages here", "this stage requires a PR." Stop reading conventions.
2. **Auto-generate prompt hints from schemas.** Delete the hand-written cheat sheet. Schemas are already authoritative — let them speak.
3. **Make stages typed.** Mark which stages produce code, which review JSON, which gate on humans. The executor handles the type-specific work itself instead of asking the agent to.
4. **Make "what success looks like" a single contract.** No more retrofitting tests for new edge cases. Define the outcome shape once; the engine and the test both validate against it.
5. **Pull HIL persistence out of the cache.** Items live on disk first, cache is a view. Tests interact with disk, not internals.
6. **Type the artefacts.** Branches know their repo. PR URLs are typed. The "wrong cwd" bug becomes a compile error.

### The honest read

V0 was the right call. We shipped fast to learn what mattered. We now know what mattered, and it's the contract layer — what stages declare, what the engine knows about them, what the test verifies against. None of that was missing from V0 by accident; it was deferred so we could see the shape of the problem first.

The shape is now clear. Roughly six weeks of focused contract work — most of it removing things from the executor rather than adding — would eliminate the entire class of bugs we just chased for two days.

The bird's-eye summary: **we're not paying for missing features. We're paying for missing contracts.**

## 1. Executive synthesis

The dogfood revealed a single recurring pressure: Hammock's stage contract is too thin, so the executor compensates with prose-prompt boilerplate, naming conventions, and silent-skip fall-through. Every iteration we hit was a place where a stage's *behavioural obligation* (push a branch, open a PR, emit `plan.yaml`, accept loop-back) lived as English in `prompt_builder.py` rather than as a typed property on `StageDefinition`. The runner could not reason about those obligations, so it either swallowed mismatches (BranchNotFoundError → silent skip, missing stage.json → outcomes special-case) or relied on convention-as-code (review-{X}-{agent,human} naming, `plan.yaml` filename, `write-` verb prefix). Each new template can break a heuristic; each new schema requires editing `_SCHEMA_HINTS`. Archon, by contrast, makes the node *kind* a discriminated union (`BashNode`/`PromptNode`/`ApprovalNode`/`ScriptNode`/`LoopNode`/`CancelNode`) and routes engine behaviour off the kind, not off prose. v0 chose "ship a thing"; the dogfood named the contracts v1 needs to make explicit.            jmnnnnnkuh 

## 2. Issues catalogued by root cause

### 2.1 Branch lifecycle gap (commit `bb380a9`)

- **Symptom**: 0 stage branches were created, no `worktree_created` events fired, outcome #11 and #13 both empty.
- **Root cause**: `_setup_stage_isolation` (`runner.py:1292`) forks from `hammock/jobs/<slug>` but nothing created that parent. `BranchNotFoundError` was caught and *logged*, then control fell through to running in the live checkout — silently.
- **Current fix**: added `_setup_job_isolation` (`runner.py:1264`) called once at job start, plus best-effort push.
- **Why patch-shaped**: it added a missing call, but the silent-skip catch is still there. `_setup_stage_isolation` still treats "parent missing" as a recoverable warning. The fix relies on the new caller running first, not on the runtime refusing to dispatch a stage whose preconditions are unmet.
- **Better fix**: a stage isolation precondition that the dispatcher *checks* before calling the agent. If the stage declares `isolation: "worktree"` and the parent branch isn't materialised, fail the stage with `state=FAILED, reason=isolation_unmet` rather than running the agent in the wrong cwd. Compare Archon's `WorktreeProvider` (`packages/isolation/src/factory.ts`) where isolation is requested via `IsolationRequest` and produces a typed `WorktreeEnvironment` — the engine only proceeds after that environment exists.

### 2.2 plan.yaml insertion-point heuristic (commits `ce50a68`, `ba07275`, `a2542ab`)

- **Symptom**: appended stages landed before the expander's review triple, so reviewers ran on an empty plan and the loop-back semantics were wrong. Then the `write-` prefix mismatched the expander's `id`, so the heuristic missed the right anchor again.
- **Root cause**: `_merge_plan_yaml_into_stage_list` (`runner.py:690-792`) reads three conventions out of prose: filename = `plan.yaml`, anchor = "any stage whose id starts with `review-{stage_def.id}-`", verb-prefix-stripping for `write-X` templates. None of these are declared.
- **Current fix**: also strip the `write-` prefix when matching reviewer ids; insert after the *last* matching review stage.
- **Why patch-shaped**: the next template that names a reviewer differently (`audit-X`, `verify-X`, no review at all) breaks the heuristic again. Strings in the runner know about strings in the template — a coupling with no type.
- **Better fix**: structural metadata on the expander stage — `expander.output_path: str` (defaults to `plan.yaml`) and `expander.insert_after: str` (an explicit stage id). Or, more powerfully: replace the flat ordered list with a DAG where the expander emits *children of itself* and review stages are wired by id, not by ordering. Archon (`packages/workflows/src/dag-executor.ts`) executes a graph in topological layers and resolves output references by `$nodeId.output` — there is no insertion-point heuristic because there is no "insertion".

### 2.3 Schema-hint prompt boilerplate (commits `9300007`, `52a074e`)

- **Symptom**: claude wrote `review-verdict-schema` JSON with 7 extra fields the Pydantic model rejected (`extra='forbid'`). Stage failed validation; rerun produced the same drift.
- **Root cause**: the prompt builder couldn't tell the agent the schema's actual shape, because the schema is a Pydantic class in `shared/artifact_validators.py` and the prompt builder doesn't read it. So `_SCHEMA_HINTS` (a hand-edited `dict[str, str]` in `prompt_builder.py:211`) lists each schema's fields and constraints by hand.
- **Current fix**: added two more entries to `_SCHEMA_HINTS`.
- **Why patch-shaped**: every new validator schema requires editing `prompt_builder.py`. Two sources of truth (the Pydantic model and the prompt hint) drift on the first iteration where someone updates one but not the other.
- **Better fix**: each registered model in `REGISTRY` exposes `prompt_hint() -> str` (or the prompt builder calls `model.model_json_schema()` and renders a curated subset — required fields, types, the `extra='forbid'` constraint). The prompt builder enumerates declared validators on the stage and looks each model up; no per-stage edits.

### 2.4 PR-creation protocol injected by string match (commit `5d9fced`)

- **Symptom**: stages that needed to push a branch and open a PR sometimes did, sometimes didn't, and when they did the URL was fabricated.
- **Root cause**: `prompt_builder.py:128-144` injects a fixed five-step protocol *if and only if* the stage description string contains "PR/pull request/merge/push/branch/commit". The decision is heuristic on the agent-author's wording, not on a typed obligation.
- **Current fix**: sharpened the boilerplate (use `gh pr create`, capture URL from stdout, never fabricate).
- **Why patch-shaped**: a stage that *should* push a branch but whose description doesn't say so gets no protocol; a review stage that mentions the word "merge" gets the protocol unnecessarily. The trigger is the wrong shape.
- **Better fix**: `StageDefinition.capabilities: list[Literal["git_push","open_pr","run_tests","write_code"]]`. The runner — not the prompt — performs the steps it can (`gh pr create`, capture URL, write to a designated output file), and only emits the agent-facing protocol fragment for capabilities the agent must actually do. Compare Archon's `BashNode`: a stage that runs shell *is its own kind*, not a prompt with shell-y wording.

### 2.5 runs_if-skipped stages have no stage.json (commit `41ff1e5`)

- **Symptom**: outcome #2 ("every stage SUCCEEDED") failed for jobs with conditionally-skipped stages, because the absent `stage.json` looked like a never-reached stage.
- **Root cause**: the dispatch path skips writing a stage record at all (`runner.py:215-218`). The outcome contract reads disk; "stage was skipped intentionally" is not on disk anywhere except in `_execute_stages`'s in-memory `dispatch_skipped` set.
- **Current fix**: `outcomes.py` infers skipping by checking whether the stage has a `runs_if` predicate and treats absent `stage.json` + non-None `runs_if` as "ok".
- **Why patch-shaped**: the test now reproduces a sliver of the runner's logic. If the runner adds a third skip path (cancellation, missing-input short-circuit), tests have to learn it too. The contract is implicit.
- **Better fix**: emit `stage.json` with `state=SKIPPED, reason="runs_if=false"` even for skipped stages. One source of truth for the outcomes module — and for the UI, and for resume. Archon writes a `nodeOutputs` entry per node regardless of outcome; downstream `$node.output` references are well-defined whether the node ran or not.

### 2.6 HIL gate stitching depends on cache internals (commit `9c738c0`)

- **Symptom**: the test posted to `/api/hil/{id}/answer` and got a 404 because the dashboard's in-memory cache hadn't picked up the new HIL artefact yet (the filesystem watcher is off in test mode).
- **Root cause**: HIL items are not authoritative on disk; the dashboard reads from a process-local cache populated at lifespan startup. The test had to reach into `app.state.cache._scan` (a private method) to force a rescan.
- **Current fix**: call `cache._scan` from `hil_stitcher.py` before posting.
- **Why patch-shaped**: the test now depends on a private API to compensate for an architectural choice (cache-as-truth) that doesn't suit external observers. Renaming `_scan` breaks the test.
- **Better fix**: either (a) make the disk-watch path required when test mode is on, or (b) expose a public `cache.refresh()` and have the test use it, or (c) have the HIL POST endpoint do an opportunistic rescan if the id isn't found. Architecturally, the issue is that "what HIL items exist" has two answers (disk and cache) and the cache is treated as authoritative. Tests reveal the leak first because they're the only client crossing the boundary.

### 2.7 Cleanup ran `git push --delete` against the wrong remote (commit `ee9ebd9`)

- **Symptom**: teardown failed silently or against the test repo's `origin` instead of the project repo's remote — branch deletion was never effective.
- **Root cause**: cleanup ran in the test root, but the branches were on the *project* remote. `git push --delete` is cwd-sensitive.
- **Current fix**: switched to `gh api -X DELETE /repos/<slug>/git/refs/heads/<branch>`, which is repo-explicit. Also closes open PRs first, since deletion fails on a branch with an open PR.
- **Why patch-shaped**: it's a correct fix, but it's correct because the test author thought of it. The runtime has no concept of "this branch lives on this remote", so neither does cleanup. Compare Archon's `pr-state.ts` (`packages/isolation/src/pr-state.ts`): branch identity is `(BranchName, RepoPath)` and cleanup queries PR state via `gh pr list --head <branch>` against the explicit repo path; the path is part of the branch's type.

### 2.8 Stop-hook stderr/stdout discoverability gap (commit `1896389`)

- **Symptom**: validation-stage stop hooks failed in ways that the operator only saw via `claude -p` stderr, not in the job event log.
- **Root cause**: hook output isn't captured into the structured event stream. The runner forwards stderr but doesn't lift it to a typed event.
- **Current fix**: the prompt now names the JOB DIR explicitly so the agent doesn't write outputs to the worktree.
- **Why patch-shaped**: it sidesteps the issue (fewer reasons for the hook to fail) instead of fixing observability.
- **Better fix**: a typed `hook_fired` event with `kind`, `stage_id`, `exit_code`, `stderr_snippet`. The dogfood spec already expects outcome #5 to assert this; the bundled validator just doesn't emit it yet.

### 2.9 JOB_DIR vs cwd confusion (`prompt_builder.py:201`)

- **Symptom**: agents wrote required outputs into the worktree (where `git status` saw them as project changes) instead of under the job's storage dir.
- **Root cause**: the prompt has two paths and the agent has to remember which is which. The `Required outputs` section now contains a paragraph telling the agent to write to JOB DIR, not the cwd.
- **Current fix**: prose disambiguation (commit `1896389`).
- **Why patch-shaped**: every stage prompt repeats the disambiguation. The agent re-derives it each time.
- **Better fix**: outputs declared on the stage carry their *kind* (`artefact` writes to JOB DIR, `code_change` writes to cwd) and the runtime resolves the absolute path. The agent receives an absolute path per output, no choice to make.

### 2.10 Convention coupling: expander → plan.yaml → review naming (combined #2.2 + the wider naming surface)

This isn't a single bug, it's the shape that produced four of them. The expander stage declares `is_expander: bool` (a single bit), but the runtime depends on six conventions: filename `plan.yaml`, schema `Plan`, child stages declared in `stages:`, anchor stage name `review-{id}-{agent,human}`, optional `write-` verb prefix, child stage ids globally unique. None of these are typed. `is_expander: bool` is the smallest possible declaration.

## 3. Architectural bottlenecks

### 3.1 Convention-as-code in the executor

`runner.py` carries roughly 40 lines of string-matching that encode template authoring conventions: stripping `write-` prefixes (`runner.py:756`), recognising review triples by id pattern, hardcoding `plan.yaml` as the expander output. This is fine for two templates and breaks at six. The deeper issue is that `StageDefinition` is a passive record — the executor projects behaviour onto it via prose-pattern matching. A new template author must read `runner.py` to learn what naming the runtime expects. Archon's `dagNodeSchema` (`packages/workflows/src/schemas/dag-node.ts`) takes the opposite stance: a node's *kind* is a typed shape, mutually exclusive at parse time via `superRefine`, and the executor branches on `isBashNode` / `isApprovalNode` / `isLoopNode` type guards, not on string patterns. There is no place where a naming convention escapes the schema.

### 3.2 Prose-prompt as last-mile glue

The protocol for "this stage must push a branch and open a PR" lives as 17 lines of English in `prompt_builder.py:128-144`, gated by a substring search on the stage description. Correctness is then one prompt-rewrite away from breaking. This is a structural symptom of *no capability typing*: the runtime cannot perform the protocol itself (it doesn't know which stages should push), so it delegates to prose, and prose is unreliable. Archon's adapters (`packages/adapters/src/forge/github.ts` etc.) and orchestrator's `prompt-builder.ts` separate "what the agent does" from "what the platform does"; PR opens go through the GitHub adapter, not through agent prose.

### 3.3 No capability typing on stages

Today every stage is "stage with required_outputs and a description". The runner can't distinguish a stage that produces code changes from a stage that reviews JSON from a stage that opens a PR. As a result, gates that the runtime *should* enforce (the stage that claimed to open a PR must produce a captured URL; the stage that claimed to push a branch must leave HEAD on the remote) become prompt assertions instead of post-conditions. Compare Archon's six-variant `DagNode` union — each variant has its own post-condition surface (Bash captures stdout/exit, Loop has `until` / `until_bash` completion detection, Approval has `on_reject`).

### 3.4 Outcomes contract is implicit

`outcomes.py` reads `stage.json` files off disk and infers correctness. When the runner added "skip without writing stage.json", outcomes had to learn that rule to avoid false-positives. There is no shared schema both produce against; instead, runtime *behaviour* is the contract and tests reverse-engineer it. Archon's `workflowRunSchema` (`packages/workflows/src/schemas/workflow-run.ts`) defines the on-disk run record and node-output records as Zod types — both engine and any reader validate against the same schema. A new state (paused, skipped) is a schema change; both producers and consumers see the diff.

### 3.5 HIL stitching depends on internal cache state

The dashboard's HIL cache is treated as authoritative, but disk is the durable source. In production a filesystem watcher reconciles them; in test the watcher is off and the cache is a black box from outside the process. The leaky abstraction surfaces because the test is the first external observer. The fix is structural: pick *one* source of truth (disk, with the cache as a deduplicator) or expose a public reconcile call. Archon avoids this by routing approval state through `workflow_runs.status='paused'` in SQLite (`packages/workflows/src/store.ts`) — there is no in-memory cache to drift.

## 4. Archon patterns worth borrowing

### 4.1 Discriminated-union node kinds

- **Where**: `packages/workflows/src/schemas/dag-node.ts:1-180`. `dagNodeSchema` is a flat schema with `superRefine` enforcing mutual exclusivity across `command`, `prompt`, `bash`, `loop`, `approval`, `cancel`, `script`. `DagNode = CommandNode | PromptNode | BashNode | LoopNode | ApprovalNode | CancelNode | ScriptNode`.
- **What it does**: each kind has its own typed payload. Engine code `if (isBashNode(node)) ... else if (isApprovalNode(node)) ...` branches on *kind*, not on description text or naming. Hammock today has one kind: "stage with `description` + `required_outputs`".
- **Hammock migration**: introduce `StageKind = "produce_artefact" | "code_change" | "review" | "expander" | "approval" | "noop"` and split `StageDefinition` into a discriminated union. `is_expander: bool` becomes `kind: "expander"` with a typed payload `{ output_path, child_insert_after }`. Reviewers become `kind: "review"` with `{ target: stage_id, verdict_schema }` — the runtime then knows how to wire loop-backs without parsing names. Files: `shared/models/stage.py`, all template YAMLs.
- **Cost**: HIGH — touches every template, the parser, the runner, and outcomes. But pays for items 2.2, 2.4, 2.10 in one move.

### 4.2 DAG with `$nodeId.output` references instead of insertion-point heuristics

- **Where**: `packages/workflows/src/dag-executor.ts:1-200` plus `condition-evaluator.ts:1-120`. Stages declare `depends_on` and `when:` expressions like `"$reviewer.output.verdict == 'passed'"`. Order is topological, not positional.
- **What it does**: an expander emits children that depend on the expander; reviewers depend on the children; gates condition on outputs. There is no "insert after" because there is no flat list. The runtime computes layers and runs each layer's nodes concurrently.
- **Hammock migration**: replace `stage-list.yaml` (an ordered list) with a graph where each stage carries `depends_on: list[str]`. The expander appends children with `depends_on: [<expander_id>]`; the reviewer auto-fits because it depends on the expander too. `runs_if` becomes a `when:` expression in the same syntax. Files: `runner.py:_execute_stages`, `runner.py:_merge_plan_yaml_into_stage_list` (deleted), `shared/models/stage.py`, `outcomes.py`.
- **Cost**: HIGH but high-leverage — kills the insertion-point heuristic, the runs_if-vs-loop_back duality, and most of the convention coupling at once.

### 4.3 Schema-derived prompt hints

- **Where**: Archon doesn't have an exact analogue (its prompts are user-authored), but the *pattern* is in `packages/workflows/src/validator.ts`: validation issues carry typed `field`/`hint`/`suggestions` derived from the Zod schema. Pydantic supports the same: `Model.model_json_schema()` plus `Field(description=...)` already encodes everything `_SCHEMA_HINTS` repeats.
- **What it does**: one source of truth for "what fields does this artefact have"; the prompt builder enumerates from the schema.
- **Hammock migration**: drop `_SCHEMA_HINTS` from `prompt_builder.py`. Have `shared/artifact_validators.REGISTRY` register `(name, model_class)` pairs; add `prompt_hint(model_cls) -> str` that walks `model_json_schema()` and renders the required-fields-only block, including the `extra='forbid'` constraint. Files: `prompt_builder.py`, `shared/artifact_validators.py`, possibly Field annotations on the existing models.
- **Cost**: LOW. ~150 lines deleted, a small renderer added. No template changes.

### 4.4 Capability-typed retry and approval

- **Where**: `packages/workflows/src/schemas/retry.ts` (`stepRetryConfigSchema`) and `packages/workflows/src/schemas/dag-node.ts` (`approvalNodeSchema` with `on_reject.prompt`, `on_reject.max_attempts`). Both are first-class node properties.
- **What it does**: retry-on-transient-error is a structural property of the node, not "the agent should try again". Approval is a node *kind* with capture and reject semantics, not a stage that happens to mention humans.
- **Hammock migration**: lift HIL into a stage kind (`kind: "approval"`) with `gate_message`, `capture_response`, `on_reject` fields. Lift retry from "the runner re-dispatches a failed stage by chance" to a declared `retry: { max_attempts, on_error: "transient" | "all" }`. Files: `shared/models/stage.py`, `runner.py:_run_single_stage`, `tests/e2e/hil_stitcher.py` (no longer needs `_scan` because approval state is in the run record).
- **Cost**: MEDIUM. Maps cleanly onto existing HIL plumbing; mostly renames + types.

### 4.5 Branch + PR identity carried as typed values, not paths

- **Where**: `packages/git` exports `BranchName` and `RepoPath` as branded types; `packages/isolation/src/pr-state.ts` queries `(branch, repoPath)` together; cleanup never has to guess a remote.
- **What it does**: a branch is always paired with the repo it lives on; commands like `git push --delete` cannot accidentally run against the wrong cwd because the path is part of the call signature.
- **Hammock migration**: introduce `Branch(repo_path: Path, name: str)` (a frozen dataclass) and require it as the argument to all branch operations in `dashboard/code/branches.py` and `tests/e2e/cleanup.py`. Removes a class of cwd bugs and makes 2.7 impossible by construction.
- **Cost**: LOW–MEDIUM. The footprint is small; the win is that 2.7's class of bug becomes a type error.

## 5. Concrete refactor sequence

Each chunk = one commit. Order is "biggest pain-to-risk ratio first."

1. **Schema-derived prompt hints (4.3)** — *Precondition*: none. *Files*: `prompt_builder.py`, `shared/artifact_validators.py`. *Why first*: zero template change, deletes ~150 lines of `_SCHEMA_HINTS`, removes the most-recent class of dogfood bug. *Test*: existing `test_prompt_builder` plus a new "schema-hint matches model" parametrised test.

2. **Emit `stage.json` for skipped stages (#2.5)** — *Precondition*: none. *Files*: `runner.py:_execute_stages`, `outcomes.py`. *Why second*: makes the skip path observable, removes the special-case from outcomes, fixes resume reasoning. *Test*: `outcomes.py` no longer references `runs_if` to detect skips; e2e outcome #2 should still pass.

3. **Branch as a typed pair (4.5)** — *Precondition*: 1, 2. *Files*: `dashboard/code/branches.py`, `tests/e2e/cleanup.py`, `runner.py:_setup_*`. *Why third*: the rest of the refactor will move stage state around; branches becoming typed first means the executor changes don't introduce new cwd bugs. *Test*: cleanup runs against an explicit `repo_slug` end-to-end.

4. **Capabilities on `StageDefinition` (4.4 partial — capabilities first, kinds later)** — *Precondition*: 3. *Files*: `shared/models/stage.py`, `prompt_builder.py:128-144`, every template YAML. *Why fourth*: kills the `if "PR" in description` substring matching. The runtime emits the protocol fragment based on `capabilities`. *Test*: a stage with `capabilities: [git_push, open_pr]` gets the protocol; a review stage doesn't.

5. **Stage kinds (4.1) — narrow first cut: `expander` and `review`** — *Precondition*: 4. *Files*: `shared/models/stage.py` (StageKind enum, payloads), `runner.py` (replace string-matching with `match stage_def.kind`), templates. *Why fifth*: kills items 2.2 and 2.10. The expander declares `expander.output: "plan.yaml"` and `expander.insert_after: <id>`; the runner reads the field, no heuristics.

6. **HIL as an `approval` kind (4.4 completion)** — *Precondition*: 5. *Files*: `shared/models/stage.py`, `runner.py`, `tests/e2e/hil_stitcher.py`. *Why sixth*: removes the `cache._scan` leak by making approval state authoritative on disk. *Test*: HIL stitcher posts to `/api/hil/.../answer` without first scanning a private cache.

7. **DAG executor (4.2)** — *Precondition*: 5, 6. *Files*: `runner.py:_execute_stages` (rewrite as topological), `shared/models/stage.py` (`depends_on`), templates. *Why last*: this is the largest change and benefits most from the prior chunks already in place. *Test*: existing e2e passes; a synthetic three-stage diamond runs in two layers.

8. **Optional follow-up: capability-typed outputs** (`output.kind: "artefact" | "code_change" | "pr_url"`) — closes the JOB_DIR/cwd disambiguation prose loop (#2.9). Cheap once 4 has landed.

## 6. v0 vs v1

The v0 design optimised for "ship enough surface to learn what matters". That was the right call: we now know which contracts are load-bearing. The dogfood sharpened them: stage *kind* (not just shape), stage *capabilities* (not prose obligation), DAG topology (not flat list with insertion-point heuristics), authoritative on-disk state (not cache-as-truth), typed branch identity (not cwd-sensitive paths). v1's job is to make those contracts explicit and let the executor reason against them, so that adding a new template is a schema-checked change, not a runner-patching exercise. The shape we want is Archon's: a node kind is a Zod-validated payload, the engine branches on the kind, and conventions don't escape the schema.
