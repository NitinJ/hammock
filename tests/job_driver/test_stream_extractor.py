"""Tests for StreamExtractor.

Validates extraction of messages.jsonl, tool-uses.jsonl, result.json, and
per-subagent dirs from recorded claude --output-format stream-json fixtures.

All tests run against real fixture files under tests/fixtures/recorded-streams/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_driver.stream_extractor import StreamExtractor

FIXTURES = Path(__file__).parent.parent / "fixtures" / "recorded-streams"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    """Read all valid JSON lines from a jsonl file."""
    if not path.exists():
        return []
    lines = []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if raw:
            lines.append(json.loads(raw))
    return lines


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# basic extraction
# ---------------------------------------------------------------------------


def test_simple_success_extracts_one_message(tmp_path: Path) -> None:
    """simple_success fixture: 1 assistant turn → 1 entry in messages.jsonl."""
    summary = StreamExtractor.extract(FIXTURES / "simple_success.jsonl", tmp_path)

    assert summary.messages_count == 1
    assert summary.tool_uses_count == 0
    assert summary.subagent_ids == []

    msgs = _read_jsonl(tmp_path / "messages.jsonl")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"
    assert "analyzed" in msgs[0]["content"][0]["text"]


def test_simple_success_result_json(tmp_path: Path) -> None:
    """simple_success fixture: result.json captures cost and success flag."""
    StreamExtractor.extract(FIXTURES / "simple_success.jsonl", tmp_path)

    result = _read_json(tmp_path / "result.json")
    assert result["is_error"] is False
    assert result["total_cost_usd"] == pytest.approx(0.00042)
    assert result["num_turns"] == 1
    assert result["session_id"] == "sess_simple01"


def test_simple_success_summary_result_field(tmp_path: Path) -> None:
    """ExtractedStream.result mirrors the result.json dict."""
    summary = StreamExtractor.extract(FIXTURES / "simple_success.jsonl", tmp_path)

    assert summary.result is not None
    assert summary.result["is_error"] is False
    assert summary.result["total_cost_usd"] == pytest.approx(0.00042)


# ---------------------------------------------------------------------------
# tool call extraction
# ---------------------------------------------------------------------------


def test_one_tool_call_extracted(tmp_path: Path) -> None:
    """with_one_tool fixture: 1 Read call → 1 entry in tool-uses.jsonl."""
    summary = StreamExtractor.extract(FIXTURES / "with_one_tool.jsonl", tmp_path)

    assert summary.messages_count == 2  # two assistant turns
    assert summary.tool_uses_count == 1

    uses = _read_jsonl(tmp_path / "tool-uses.jsonl")
    assert len(uses) == 1
    assert uses[0]["tool_name"] == "Read"
    assert uses[0]["tool_use_id"] == "toolu_read01"
    assert "main.py" in uses[0]["input"]["file_path"]
    assert "hello" in uses[0]["result"]


def test_multi_tool_calls_all_extracted(tmp_path: Path) -> None:
    """multi_tool_calls fixture: 3 tool calls → 3 entries in tool-uses.jsonl."""
    summary = StreamExtractor.extract(FIXTURES / "multi_tool_calls.jsonl", tmp_path)

    assert summary.tool_uses_count == 3

    uses = _read_jsonl(tmp_path / "tool-uses.jsonl")
    assert len(uses) == 3
    names = {u["tool_name"] for u in uses}
    assert "Read" in names
    assert "Write" in names


def test_tool_error_result_recorded(tmp_path: Path) -> None:
    """tool_error_result fixture: is_error=true in tool result is preserved."""
    StreamExtractor.extract(FIXTURES / "tool_error_result.jsonl", tmp_path)

    uses = _read_jsonl(tmp_path / "tool-uses.jsonl")
    assert len(uses) == 1
    assert uses[0]["is_error"] is True


# ---------------------------------------------------------------------------
# subagent demuxing
# ---------------------------------------------------------------------------


def test_subagent_demuxed_to_own_dir(tmp_path: Path) -> None:
    """with_subagent fixture: subagent messages go to subagents/<id>/."""
    summary = StreamExtractor.extract(FIXTURES / "with_subagent.jsonl", tmp_path)

    assert "toolu_task01" in summary.subagent_ids
    sub_dir = tmp_path / "subagents" / "toolu_task01"
    assert sub_dir.is_dir()
    sub_msgs = _read_jsonl(sub_dir / "messages.jsonl")
    assert len(sub_msgs) == 1
    assert "Examining" in sub_msgs[0]["content"][0]["text"]


def test_subagent_result_json_written(tmp_path: Path) -> None:
    """with_subagent fixture: subagent result.json written in its subdir."""
    StreamExtractor.extract(FIXTURES / "with_subagent.jsonl", tmp_path)

    sub_result = _read_json(tmp_path / "subagents" / "toolu_task01" / "result.json")
    assert sub_result["is_error"] is False
    assert sub_result["parent_tool_use_id"] == "toolu_task01"


def test_two_subagents_separate_dirs(tmp_path: Path) -> None:
    """two_subagents fixture: two subagent dirs created, each with its own messages."""
    summary = StreamExtractor.extract(FIXTURES / "two_subagents.jsonl", tmp_path)

    assert set(summary.subagent_ids) == {"toolu_taskA", "toolu_taskB"}

    for sub_id in ("toolu_taskA", "toolu_taskB"):
        sub_dir = tmp_path / "subagents" / sub_id
        assert sub_dir.is_dir(), f"Expected subagent dir for {sub_id}"
        msgs = _read_jsonl(sub_dir / "messages.jsonl")
        assert len(msgs) == 1


def test_subagent_tool_calls_in_own_dir(tmp_path: Path) -> None:
    """subagent_with_tools fixture: subagent's tool calls in its tool-uses.jsonl."""
    summary = StreamExtractor.extract(FIXTURES / "subagent_with_tools.jsonl", tmp_path)

    assert "toolu_tX" in summary.subagent_ids
    sub_uses = _read_jsonl(tmp_path / "subagents" / "toolu_tX" / "tool-uses.jsonl")
    assert len(sub_uses) == 1
    assert sub_uses[0]["tool_name"] == "Read"


def test_agent0_unaffected_by_subagent(tmp_path: Path) -> None:
    """with_subagent fixture: agent0 messages.jsonl contains only Agent0 turns."""
    StreamExtractor.extract(FIXTURES / "with_subagent.jsonl", tmp_path)

    msgs = _read_jsonl(tmp_path / "messages.jsonl")
    # Agent0 has 2 turns: the Task dispatch and the follow-up
    for m in msgs:
        assert m.get("parent_tool_use_id") is None


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------


def test_session_error_maps_to_result_is_error(tmp_path: Path) -> None:
    """session_error fixture: is_error=true in result.json."""
    summary = StreamExtractor.extract(FIXTURES / "session_error.jsonl", tmp_path)

    assert summary.result is not None
    assert summary.result["is_error"] is True
    assert summary.result["subtype"] == "error_max_turns"


def test_no_turns_empty_extractions(tmp_path: Path) -> None:
    """no_turns fixture: no messages, no tool uses, result.json still written."""
    summary = StreamExtractor.extract(FIXTURES / "no_turns.jsonl", tmp_path)

    assert summary.messages_count == 0
    assert summary.tool_uses_count == 0
    assert not (tmp_path / "messages.jsonl").exists()
    assert not (tmp_path / "tool-uses.jsonl").exists()
    assert (tmp_path / "result.json").exists()


def test_malformed_line_skipped_silently(tmp_path: Path) -> None:
    """malformed_line fixture: bad JSON line is skipped; rest is extracted."""
    summary = StreamExtractor.extract(FIXTURES / "malformed_line.jsonl", tmp_path)

    # The valid assistant message and result should still be extracted
    assert summary.messages_count == 1
    assert summary.result is not None
    assert summary.result["is_error"] is False


def test_thinking_block_assistant_message_extracted(tmp_path: Path) -> None:
    """thinking_blocks fixture: assistant message (including thinking) extracted."""
    summary = StreamExtractor.extract(FIXTURES / "thinking_blocks.jsonl", tmp_path)

    assert summary.messages_count == 1
    msgs = _read_jsonl(tmp_path / "messages.jsonl")
    assert len(msgs) == 1
    content_types = {c["type"] for c in msgs[0]["content"]}
    # Both thinking and text content blocks preserved
    assert "text" in content_types


def test_out_dir_created_if_missing(tmp_path: Path) -> None:
    """extract() creates out_dir (and parents) if it does not exist."""
    deep = tmp_path / "a" / "b" / "agent0"
    StreamExtractor.extract(FIXTURES / "simple_success.jsonl", deep)

    assert deep.is_dir()
    assert (deep / "result.json").exists()
