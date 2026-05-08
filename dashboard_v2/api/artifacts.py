"""Artifact upload helpers — sanitize + save to <job_dir>/inputs/."""

from __future__ import annotations

import re
from pathlib import Path

from hammock_v2.engine import paths

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_NAME_LEN = 255
_MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB cap across all uploads per job
_DEFAULT_NAME = "artifact"


def sanitize_filename(name: str) -> str:
    """Reduce a filename to a safe relative leaf name.

    - Strips path separators (`/`, `\\`).
    - Strips control chars and replaces non-[A-Za-z0-9._-] with `_`.
    - Removes leading dots so we never get a hidden / `.dotfile`.
    - Caps to 255 chars.
    - Falls back to ``artifact`` on empty result.
    """
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1]
    # Strip control chars
    leaf = "".join(ch for ch in leaf if ord(ch) >= 0x20)
    leaf = _SAFE_RE.sub("_", leaf)
    leaf = leaf.lstrip(".")
    leaf = leaf.strip("_") or _DEFAULT_NAME
    if len(leaf) > _MAX_NAME_LEN:
        # Keep extension if there is one.
        if "." in leaf:
            head, _, ext = leaf.rpartition(".")
            ext = ext[: max(0, _MAX_NAME_LEN - 1)]
            head = head[: _MAX_NAME_LEN - len(ext) - 1]
            leaf = f"{head}.{ext}"
        else:
            leaf = leaf[:_MAX_NAME_LEN]
    return leaf


def save_artifacts(
    *,
    slug: str,
    files: list[tuple[str, bytes]],
    root: Path,
) -> list[tuple[str, int]]:
    """Save files to ``<job_dir>/inputs/<sanitized>``.

    Returns a list of ``(saved_filename, bytes_written)`` for the caller.

    Raises ``ValueError`` if total bytes exceed the cap.
    """
    if not files:
        return []
    total = sum(len(content) for _, content in files)
    if total > _MAX_TOTAL_BYTES:
        raise ValueError(f"total artifact size {total} bytes exceeds cap {_MAX_TOTAL_BYTES} bytes")
    target_dir = paths.ensure_inputs_dir(slug, root=root)
    saved: list[tuple[str, int]] = []
    used: set[str] = set()
    for orig_name, content in files:
        name = sanitize_filename(orig_name)
        # Disambiguate collisions: foo.txt, foo-1.txt, foo-2.txt, ...
        if name in used:
            n = 1
            while True:
                if "." in name:
                    head, _, ext = name.rpartition(".")
                    candidate = f"{head}-{n}.{ext}"
                else:
                    candidate = f"{name}-{n}"
                if candidate not in used:
                    name = candidate
                    break
                n += 1
        used.add(name)
        target = target_dir / name
        target.write_bytes(content)
        saved.append((name, len(content)))
    return saved


__all__ = ["sanitize_filename", "save_artifacts"]
