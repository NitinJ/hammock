"""Stage 5 manual smoke.

Exercises StreamExtractor against the recorded-stream fixtures shipped with
the test suite. No real Claude Code installation required — demonstrates the
full extraction pipeline: raw stream.jsonl → messages.jsonl, tool-uses.jsonl,
result.json, and per-subagent dirs.

Run with::

    uv run python scripts/manual-smoke-stage05.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from job_driver.stream_extractor import StreamExtractor  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "recorded-streams"


def _ok(label: str) -> None:
    print(f"  ✓ {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  ✗ {label}" + (f": {detail}" if detail else ""))
    raise SystemExit(1)


def _read_jsonl(path: Path) -> list[dict]:  # type: ignore[type-arg]
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _check_simple_success(tmp: Path) -> None:
    print("\n[simple_success] basic extraction")
    out = tmp / "simple_success"
    summary = StreamExtractor.extract(FIXTURES / "simple_success.jsonl", out)

    if summary.messages_count != 1:
        _fail("messages_count", f"expected 1, got {summary.messages_count}")
    _ok("1 assistant message extracted")

    if summary.tool_uses_count != 0:
        _fail("tool_uses_count", f"expected 0, got {summary.tool_uses_count}")
    _ok("0 tool uses (text-only reply)")

    result = json.loads((out / "result.json").read_text())
    if result.get("is_error"):
        _fail("result.is_error should be false")
    cost = result.get("total_cost_usd", 0)
    _ok(f"result.json written — cost=${cost:.5f}, session={result['session_id']}")


def _check_with_one_tool(tmp: Path) -> None:
    print("\n[with_one_tool] tool-use correlation")
    out = tmp / "with_one_tool"
    summary = StreamExtractor.extract(FIXTURES / "with_one_tool.jsonl", out)

    if summary.messages_count != 2:
        _fail("messages_count", f"expected 2, got {summary.messages_count}")
    _ok("2 assistant turns (dispatch + follow-up)")

    if summary.tool_uses_count != 1:
        _fail("tool_uses_count", f"expected 1, got {summary.tool_uses_count}")

    uses = _read_jsonl(out / "tool-uses.jsonl")
    u = uses[0]
    if u["tool_name"] != "Read":
        _fail("tool_name", f"expected Read, got {u['tool_name']}")
    _ok(f"tool-uses.jsonl: Read({u['input']['file_path']!r}) → {u['result']!r}")


def _check_subagent(tmp: Path) -> None:
    print("\n[with_subagent] subagent demuxing")
    out = tmp / "with_subagent"
    summary = StreamExtractor.extract(FIXTURES / "with_subagent.jsonl", out)

    if "toolu_task01" not in summary.subagent_ids:
        _fail("subagent_ids", f"toolu_task01 not found in {summary.subagent_ids}")
    _ok(f"subagent dirs: {summary.subagent_ids}")

    sub_dir = out / "subagents" / "toolu_task01"
    sub_msgs = _read_jsonl(sub_dir / "messages.jsonl")
    if not sub_msgs:
        _fail("subagent messages.jsonl empty")
    _ok(f"subagents/toolu_task01/messages.jsonl: {len(sub_msgs)} msg(s)")

    agent0_msgs = _read_jsonl(out / "messages.jsonl")
    for m in agent0_msgs:
        if m.get("parent_tool_use_id") is not None:
            _fail("agent0 messages.jsonl contains subagent messages")
    _ok("agent0 messages.jsonl contains only Agent0 turns")

    sub_result = json.loads((sub_dir / "result.json").read_text())
    if sub_result.get("parent_tool_use_id") != "toolu_task01":
        _fail("subagent result.json parent_tool_use_id mismatch")
    _ok("subagents/toolu_task01/result.json written with correct parent_tool_use_id")


def _check_two_subagents(tmp: Path) -> None:
    print("\n[two_subagents] parallel subagent demuxing")
    out = tmp / "two_subagents"
    summary = StreamExtractor.extract(FIXTURES / "two_subagents.jsonl", out)

    expected = {"toolu_taskA", "toolu_taskB"}
    if set(summary.subagent_ids) != expected:
        _fail("subagent_ids", f"expected {expected}, got {set(summary.subagent_ids)}")
    _ok(f"both subagent dirs created: {sorted(summary.subagent_ids)}")


def _check_session_error(tmp: Path) -> None:
    print("\n[session_error] error session mapping")
    out = tmp / "session_error"
    summary = StreamExtractor.extract(FIXTURES / "session_error.jsonl", out)

    if not summary.result or not summary.result.get("is_error"):
        _fail("result.is_error should be true")
    _ok(f"is_error=true, subtype={summary.result['subtype']!r}")


def _check_multi_tool(tmp: Path) -> None:
    print("\n[multi_tool_calls] multiple tool calls")
    out = tmp / "multi_tool_calls"
    summary = StreamExtractor.extract(FIXTURES / "multi_tool_calls.jsonl", out)

    if summary.tool_uses_count != 3:
        _fail("tool_uses_count", f"expected 3, got {summary.tool_uses_count}")
    uses = _read_jsonl(out / "tool-uses.jsonl")
    names = [u["tool_name"] for u in uses]
    _ok(f"3 tool uses extracted: {names}")


def _check_malformed(tmp: Path) -> None:
    print("\n[malformed_line] malformed line skipped silently")
    out = tmp / "malformed_line"
    summary = StreamExtractor.extract(FIXTURES / "malformed_line.jsonl", out)

    if summary.messages_count != 1:
        _fail("messages_count", f"expected 1, got {summary.messages_count}")
    if summary.result is None or summary.result.get("is_error"):
        _fail("result should be present and not error")
    _ok("bad JSON line skipped; valid message + result extracted")


def _check_thinking_blocks(tmp: Path) -> None:
    print("\n[thinking_blocks] thinking content preserved")
    out = tmp / "thinking_blocks"
    StreamExtractor.extract(FIXTURES / "thinking_blocks.jsonl", out)

    msgs = _read_jsonl(out / "messages.jsonl")
    if not msgs:
        _fail("messages.jsonl empty")
    content_types = {c["type"] for c in msgs[0]["content"]}
    if "thinking" not in content_types:
        _fail("thinking block not preserved in messages.jsonl")
    _ok(f"assistant message content types: {sorted(content_types)}")


def main() -> int:
    print("=== Stage 05 smoke: StreamExtractor ===")
    with tempfile.TemporaryDirectory(prefix="hammock-smoke05-") as tmp_str:
        tmp = Path(tmp_str)
        _check_simple_success(tmp)
        _check_with_one_tool(tmp)
        _check_subagent(tmp)
        _check_two_subagents(tmp)
        _check_session_error(tmp)
        _check_multi_tool(tmp)
        _check_malformed(tmp)
        _check_thinking_blocks(tmp)

    print("\nsmoke OK: StreamExtractor extracted all fixture scenarios correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
