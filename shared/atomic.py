"""Atomic write helpers.

Single-writer-per-file is the discipline; atomic replace is the implementation.
Each helper writes to a temp sibling, fsyncs, then renames into place. Crashes
mid-write leave the destination either at its prior content or absent — never
half-written.

Append-only logs (``*.jsonl``) use a different pattern: open in append mode,
write one line, fsync. Append + fsync of a single line is atomic on POSIX
when the line is shorter than ``PIPE_BUF`` (4 KiB on Linux); we accept that
constraint and validate at write time.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel

_PIPE_BUF_SAFE = 4000  # leaves headroom under the 4096-byte POSIX guarantee


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically.

    The destination directory must already exist. Uses ``tempfile`` to create
    a sibling temp file, writes + fsyncs, then ``os.replace`` into place.
    Survives a mid-write crash: the destination is never partially written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Clean up temp file if anything went wrong before replace
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def atomic_write_json(path: Path, model: BaseModel, *, indent: int | None = 2) -> None:
    """Serialize *model* via ``model_dump_json`` and write atomically."""
    payload = model.model_dump_json(indent=indent)
    atomic_write_text(path, payload + "\n")


def atomic_append_jsonl(path: Path, model: BaseModel) -> None:
    """Append a single JSON line to *path*.

    Writes ``model.model_dump_json()`` followed by ``\\n``. Validates the line
    is under the POSIX ``PIPE_BUF`` atomicity guarantee; raises ValueError if
    the serialised line would exceed it (rare in practice; indicates the model
    is too large for jsonl semantics and likely belongs in a side file).
    """
    line = model.model_dump_json()
    encoded = (line + "\n").encode("utf-8")
    if len(encoded) > _PIPE_BUF_SAFE:
        raise ValueError(
            f"jsonl line too large ({len(encoded)} bytes, limit {_PIPE_BUF_SAFE}); "
            "model exceeds atomic-append guarantee"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    # Open with O_APPEND for atomic append semantics
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
