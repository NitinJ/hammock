# Stage 5 — CLI session spawning + observability extraction

**PR:** TBD (in PR)
**Branch:** `feat/stage-05-cli-session`

## What was built

`RealStageRunner` replaces the `FakeStageRunner` stub: it spawns a real `claude` subprocess per stage using `--output-format stream-json`, captures the raw JSONL output, and hands it to `StreamExtractor` for structured parsing. The `FakeStageRunner` is retained — all Stage 4 tests remain as regression guards.

- **`job_driver/stream_extractor.py`** — `StreamExtractor.extract(stream_jsonl_path, out_dir)` reads the raw `stream.jsonl`, routes each event by `parent_tool_use_id` (null → Agent0, non-null → a named subagent), and writes:
  - `messages.jsonl` — one entry per assistant turn (full content: text, thinking, tool_use blocks preserved)
  - `tool-uses.jsonl` — one entry per matched tool-call+result pair (correlated by `tool_use_id`)
  - `result.json` — session-end summary (cost, tokens, error flag, exit subtype)
  - `subagents/<tool_use_id>/` — identical layout for each spawned subagent context

- **`job_driver/stage_runner.py`** — `RealStageRunner(project_root, claude_binary, stop_hook_path)`:
  - Writes `stage_run_dir/session-settings.json` (Stop hook wiring, if `stop_hook_path` is set).
  - Builds `[claude, -p, <description>, --output-format, stream-json, --settings, <path>]`.
  - Spawns via `asyncio.create_subprocess_exec` with `stdout=PIPE`; streams each line to `agent0/stream.jsonl` using `O_WRONLY|O_CREAT|O_APPEND` + `fsync`.
  - After subprocess exit, calls `StreamExtractor.extract()` and maps the result to `StageResult`.
  - Injects `HAMMOCK_JOB_DIR`, `HAMMOCK_STAGE_ID`, `HAMMOCK_STAGE_REQUIRED_OUTPUTS` into the subprocess environment.

- **`hammock/hooks/validate-stage-exit.sh`** — Stop hook script. Reads `HAMMOCK_JOB_DIR` and `HAMMOCK_STAGE_REQUIRED_OUTPUTS` (newline-separated paths). If any required output is absent, prints a diagnostic and exits 2 (block session exit). Exits 0 (pass) otherwise.

- **10 JSONL fixture files** under `tests/fixtures/recorded-streams/`: `simple_success`, `with_one_tool`, `with_subagent`, `two_subagents`, `session_error`, `no_turns`, `multi_tool_calls`, `subagent_with_tools`, `malformed_line`, `thinking_blocks`, `tool_error_result`. These are minimal but valid recordings of `claude --output-format stream-json` output shapes. Tests use fake claude binaries (`cat <fixture>`) so no real API key is needed.

- **25 new tests** — `tests/job_driver/test_stream_extractor.py` (16 tests: basic extraction, tool-use correlation, subagent demuxing, edge cases) and `tests/job_driver/test_real_stage_runner.py` (9 tests: subprocess spawn, stream capture, stop hook wiring, env var injection).

- **`scripts/manual-smoke-stage05.py`** — runs `StreamExtractor` against all fixture scenarios and asserts expected extraction results; no subprocess or API key needed.

## Notable design decisions made during implementation

1. **`parent_tool_use_id` is the sole demux key.** The design doc specifies this field; there is no secondary routing by `session_id`. Every event with a non-null `parent_tool_use_id` is routed to the subagent context keyed by that ID, regardless of nesting depth. Deeply-nested sub-sub-agents would route to whichever task dispatch they inherit — fine for v0.

2. **Tool use correlation via `_ContextState._pending`.** `tool_use` blocks appear inside assistant messages; their matching `tool_result` blocks arrive in a later user message. `_ContextState` keeps a `dict[tool_use_id → tool_use_block]` that is populated when the assistant message is processed and drained when the user message arrives. If a result arrives with no matching pending entry (e.g., the stream was truncated before we started tracking), the entry is written with `tool_name=None` and `input=None` rather than silently dropped.

3. **`outputs_produced=[]` for RealStageRunner.** The runner cannot know which files the agent wrote — file tracking is done via MCP task records (Stage 6). The `_stage_already_succeeded()` check in `runner.py` validates required outputs separately by testing path existence in `job_dir`.

4. **`stage_def.description or stage_def.id` as the initial prompt.** Stage 5 has no specialist resolution; Stage 6 wires the full prompt construction pipeline via the MCP server. Using the description is the minimal correct stub.

5. **`--channels dashboard` omitted.** No MCP server is running in Stage 5. Stage 6 adds the server and re-introduces this flag.

6. **Fake claude binary pattern for tests.** Rather than mocking `asyncio.create_subprocess_exec` internals (fragile, opaque), tests write real shell scripts that `cat` a fixture file. This exercises the actual subprocess spawn, stream capture, and file-write code path with zero mocking.

7. **`fsync` per line in `_append_json_line`.** Observer processes (a future tail reader) need consistent line boundaries. `O_APPEND` + `fsync` gives POSIX atomic line writes up to `PIPE_BUF` (4K) and ensures each line is durable before the next is written.

8. **`stream.jsonl` is never touched by the extractor.** It is the raw authority. Extraction is post-run and idempotent; re-running on the same `stream.jsonl` overwrites prior extractions with identical content.

## Locked for downstream stages

- **`StreamExtractor.extract(stream_jsonl_path, out_dir) -> ExtractedStream` is stable.** Stage 6 and later may call it directly; the signature and `ExtractedStream` fields are fixed.
- **Output layout is canonical.** `agent0/stream.jsonl`, `agent0/messages.jsonl`, `agent0/tool-uses.jsonl`, `agent0/result.json`, `agent0/subagents/<id>/` — these paths are referenced in `shared/paths.py` (`agent0_messages_jsonl`, `agent0_tool_uses_jsonl`, `agent0_subagent_dir`). Don't reorganise without a structural-change stage.
- **`StageRunner` Protocol is unchanged.** `async def run(stage_def, job_dir, stage_run_dir) -> StageResult`. Stage 6 adds `RealStageRunner` construction args but not Protocol changes.
- **`session-settings.json` Stop hook format.** `{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bash <path>"}]}]}}`. The outer array is the hook-group list; the inner `hooks` key is the hook entries list. Exactly as Claude Code expects.

## Files added/modified (14)

```
job_driver/stream_extractor.py                      (new)
job_driver/stage_runner.py                          (+RealStageRunner)

hammock/hooks/validate-stage-exit.sh                (new, chmod +x)

tests/fixtures/recorded-streams/simple_success.jsonl
tests/fixtures/recorded-streams/with_one_tool.jsonl
tests/fixtures/recorded-streams/with_subagent.jsonl
tests/fixtures/recorded-streams/two_subagents.jsonl
tests/fixtures/recorded-streams/session_error.jsonl
tests/fixtures/recorded-streams/no_turns.jsonl
tests/fixtures/recorded-streams/multi_tool_calls.jsonl
tests/fixtures/recorded-streams/subagent_with_tools.jsonl
tests/fixtures/recorded-streams/malformed_line.jsonl
tests/fixtures/recorded-streams/thinking_blocks.jsonl
tests/fixtures/recorded-streams/tool_error_result.jsonl

tests/job_driver/test_stream_extractor.py           (new, 16 tests)
tests/job_driver/test_real_stage_runner.py          (new, 9 tests)

scripts/manual-smoke-stage05.py

docs/stages/stage-05.md                             (this file)
docs/stages/README.md                               (index updated)
```

## Dependencies introduced

None — all Stage 5 code is pure Python stdlib + existing project deps.

## Acceptance criteria — met

- [x] `claude --output-format stream-json` stdout captured line-by-line to `stream.jsonl`
- [x] `StreamExtractor` writes `messages.jsonl`, `tool-uses.jsonl`, `result.json` from raw stream
- [x] Subagent events demuxed to `subagents/<parent_tool_use_id>/` subdirs
- [x] Tool-use call+result pairs correlated by `tool_use_id`
- [x] `result.is_error=true` → `StageResult.succeeded=False`; non-zero exit code → `StageResult.succeeded=False`
- [x] `total_cost_usd` reflected in `StageResult.cost_usd`
- [x] Stop hook wired via `session-settings.json` when `stop_hook_path` set
- [x] `HAMMOCK_JOB_DIR`, `HAMMOCK_STAGE_ID`, `HAMMOCK_STAGE_REQUIRED_OUTPUTS` passed to subprocess
- [x] Malformed JSONL lines skipped silently; rest extracted correctly
- [x] All 431 tests pass; ruff + pyright clean

## Smoke output

```
=== Stage 05 smoke: StreamExtractor ===

[simple_success] basic extraction
  ✓ 1 assistant message extracted
  ✓ 0 tool uses (text-only reply)
  ✓ result.json written — cost=$0.00042, session=sess_simple01

[with_one_tool] tool-use correlation
  ✓ 2 assistant turns (dispatch + follow-up)
  ✓ tool-uses.jsonl: Read('/tmp/project/main.py') → "def main():\n    print('hello')\n"

[with_subagent] subagent demuxing
  ✓ subagent dirs: ['toolu_task01']
  ✓ subagents/toolu_task01/messages.jsonl: 1 msg(s)
  ✓ agent0 messages.jsonl contains only Agent0 turns
  ✓ subagents/toolu_task01/result.json written with correct parent_tool_use_id

[two_subagents] parallel subagent demuxing
  ✓ both subagent dirs created: ['toolu_taskA', 'toolu_taskB']

[session_error] error session mapping
  ✓ is_error=true, subtype='error_max_turns'

[multi_tool_calls] multiple tool calls
  ✓ 3 tool uses extracted: ['Read', 'Read', 'Write']

[malformed_line] malformed line skipped silently
  ✓ bad JSON line skipped; valid message + result extracted

[thinking_blocks] thinking content preserved
  ✓ assistant message content types: ['text', 'thinking']

smoke OK: StreamExtractor extracted all fixture scenarios correctly
```

## Notes for downstream stages

- **Stage 6 (specialist resolution + MCP server)**: wire `--channels dashboard` flag pointing at the Hammock MCP server. Replace `stage_def.description or stage_def.id` with the full specialist-resolved prompt. Add `outputs_produced` tracking via MCP task records — `StageResult.outputs_produced` is currently always `[]`.
- **Stage 6 also owns the Stop hook upgrade**: `validate-stage-exit.sh` currently checks only required output paths. Stage 6 may extend it with MCP task status validation (all declared tasks completed) before allowing session exit.
- **Tail readers** (Stage 10 SSE, any future log viewer): `messages.jsonl` and `tool-uses.jsonl` use `O_APPEND` + `fsync` writes, giving POSIX atomic line boundaries. Safe to tail without a read lock, but only after the subprocess exits (Stage 5 writes post-run, not live).
- **Live extraction** (future): `StreamExtractor` is post-run only. If you need live progress events (e.g., streaming messages as the agent runs), you'll need to restructure to call `_ContextState` handlers inside the subprocess stdout loop in `RealStageRunner.run()`. The state classes are designed for this; extraction just needs to be called incrementally.
- **Subagent output layout is flat, not nested.** `subagents/toolu_taskA/` and `subagents/toolu_taskB/` are siblings regardless of whether taskB was dispatched by taskA. If you need nested subagent trees, the `parent_tool_use_id` chain would have to be resolved into a tree before writing dirs.
