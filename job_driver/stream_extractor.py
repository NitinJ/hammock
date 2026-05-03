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
    result: dict | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _append_json_line(path: Path, data: dict) -> None:
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


def _write_json_file(path: Path, data: dict) -> None:
    """Write data as indented JSON to path atomically."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


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
        raise NotImplementedError
