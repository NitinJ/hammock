"""Stream extractor for claude --output-format stream-json output.

Per design doc § Observability — the Job Driver tails Agent0's stdout and
extracts structured data from the raw stream.jsonl file.

Output layout (all relative to ``out_dir`` = ``stage_run/latest/agent0/``):
    messages.jsonl          — one assistant-turn record per line
    tool-uses.jsonl         — one tool-call+result record per line
    result.json             — session-end summary (cost, tokens, exit code)
    subagents/<tool_use_id>/
        messages.jsonl
        tool-uses.jsonl
        result.json

``stream.jsonl`` is the unmodified raw output — never touched by this module.
``ExtractedStream`` is a lightweight summary returned by ``extract()``.

Subagent demuxing uses ``parent_tool_use_id``: events with a non-null
``parent_tool_use_id`` belong to the subagent spawned by that task dispatch.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.atomic import atomic_write_text

_PIPE_BUF_SAFE = 4000


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class ExtractedStream:
    """Lightweight summary of what was extracted from a stream.jsonl."""

    messages_count: int = 0
    tool_uses_count: int = 0
    subagent_ids: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _append_json_line(path: Path, data: dict[str, Any]) -> None:
    """Append one JSON line to path with fsync (POSIX atomic under PIPE_BUF)."""
    line = json.dumps(data, ensure_ascii=False)
    encoded = (line + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    """Write data as indented JSON to path atomically."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Per-context extraction state
# ---------------------------------------------------------------------------


class _ContextState:
    """Tracks pending tool uses for one context (Agent0 or a subagent).

    Tool use blocks appear in assistant messages; the matching tool results
    appear in subsequent user messages. This class pairs them up.
    """

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.messages_count = 0
        self.tool_uses_count = 0
        # tool_use_id → tool_use block dict (waiting for its result)
        self._pending: dict[str, dict[str, Any]] = {}

    def handle_assistant(self, event: dict[str, Any]) -> None:
        """Process an assistant event — extract text turns and queue tool uses."""
        msg = event.get("message") or {}
        content = msg.get("content") or []

        # Extract the tool_use blocks and queue them
        tool_use_blocks = [b for b in content if b.get("type") == "tool_use"]
        for block in tool_use_blocks:
            self._pending[block["id"]] = block

        # Write the full message as a messages.jsonl entry
        # Include any content type (text, thinking, tool_use) — the raw message
        # is preserved; downstream consumers filter what they need.
        entry = {
            "role": "assistant",
            "content": content,
            "session_id": event.get("session_id"),
            "model": msg.get("model"),
            "usage": msg.get("usage"),
            "parent_tool_use_id": event.get("parent_tool_use_id"),
        }
        _append_json_line(self.out_dir / "messages.jsonl", entry)
        self.messages_count += 1

    def handle_user(self, event: dict[str, Any]) -> None:
        """Process a user event — match tool results to pending tool uses."""
        msg = event.get("message") or {}
        content = msg.get("content") or []

        for block in content:
            if block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id", "")
            pending = self._pending.pop(tool_use_id, None)
            if pending is None:
                # Tool use was dispatched before we started tracking; record
                # what we have (result only).
                entry = {
                    "tool_use_id": tool_use_id,
                    "tool_name": None,
                    "input": None,
                    "result": block.get("content"),
                    "is_error": block.get("is_error", False),
                    "session_id": event.get("session_id"),
                }
            else:
                entry = {
                    "tool_use_id": tool_use_id,
                    "tool_name": pending.get("name"),
                    "input": pending.get("input"),
                    "result": block.get("content"),
                    "is_error": block.get("is_error", False),
                    "session_id": event.get("session_id"),
                }
            _append_json_line(self.out_dir / "tool-uses.jsonl", entry)
            self.tool_uses_count += 1

    def handle_result(
        self, event: dict[str, Any], result_override: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Write result.json and return the result dict."""
        data = dict(event)
        if result_override:
            data.update(result_override)
        _write_json_file(self.out_dir / "result.json", data)
        return data


# ---------------------------------------------------------------------------
# StreamExtractor
# ---------------------------------------------------------------------------


class StreamExtractor:
    """Process a completed stream.jsonl into structured extraction files.

    Designed for post-session extraction (called after subprocess exits).
    All extraction is idempotent: re-running on the same stream.jsonl
    overwrites prior extractions with the same content.
    """

    @staticmethod
    def extract(stream_jsonl_path: Path, out_dir: Path) -> ExtractedStream:
        """Read stream_jsonl_path; write messages.jsonl, tool-uses.jsonl,
        result.json, and subagents/<id>/ dirs into out_dir.

        Malformed lines are skipped silently (stream.jsonl is the authority;
        extraction is best-effort).

        Args:
            stream_jsonl_path: Path to the raw stream.jsonl captured from claude.
            out_dir: Destination directory (``stage_run/latest/agent0/``).

        Returns:
            Summary of what was extracted.
        """
        out_dir.mkdir(parents=True, exist_ok=True)

        # Agent0 context (parent_tool_use_id is null)
        agent0 = _ContextState(out_dir)
        # Subagent contexts keyed by parent_tool_use_id
        subagents: dict[str, _ContextState] = {}

        top_level_result: dict[str, Any] | None = None

        if not stream_jsonl_path.exists():
            return ExtractedStream()

        for raw_line in stream_jsonl_path.read_text().splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue  # skip malformed lines silently

            event_type = event.get("type")
            parent_id: str | None = event.get("parent_tool_use_id") or None

            # Route to the correct context
            if parent_id is not None:
                if parent_id not in subagents:
                    sub_dir = out_dir / "subagents" / parent_id
                    sub_dir.mkdir(parents=True, exist_ok=True)
                    subagents[parent_id] = _ContextState(sub_dir)
                ctx = subagents[parent_id]
            else:
                ctx = agent0

            if event_type == "assistant":
                ctx.handle_assistant(event)
            elif event_type == "user":
                ctx.handle_user(event)
            elif event_type == "result":
                result_data = ctx.handle_result(event)
                if parent_id is None:
                    # Top-level session result
                    top_level_result = result_data

        return ExtractedStream(
            messages_count=agent0.messages_count,
            tool_uses_count=agent0.tool_uses_count,
            subagent_ids=list(subagents.keys()),
            result=top_level_result,
        )
