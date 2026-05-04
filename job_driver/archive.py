"""Run-archive integrity manifest.

Per `docs/v0-alignment-report.md` Plan #5: every closed stage run dir
gets a `manifest.json` next to `agent0/` so replay tooling can detect
bit-rot. Computes SHA-256 over every file under `agent0/` (recursively,
so subagent dirs are covered) and records the digest keyed by path
relative to the stage run dir.

The manifest is intentionally a flat `{path: digest}` map plus an
`algorithm` field. No per-file size / mtime — those are not part of
the integrity contract; only the byte-content hash is.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.atomic import atomic_write_json


class ArchiveManifest(BaseModel):
    """Flat per-file digest record for a single stage run."""

    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["sha256"] = "sha256"
    files: dict[str, str] = Field(default_factory=dict)


_CHUNK = 64 * 1024


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_manifest(stage_run_dir: Path) -> ArchiveManifest:
    """Walk ``stage_run_dir/agent0/`` recursively and return a manifest.

    If ``agent0/`` doesn't exist (e.g. fake-runner stage runs), returns an
    empty manifest. Symlinks are followed only if the target is inside
    ``stage_run_dir`` — out-of-tree symlinks are skipped to avoid
    accidental hashing of project files.
    """
    agent0 = stage_run_dir / "agent0"
    files: dict[str, str] = {}
    if agent0.is_dir():
        base = stage_run_dir.resolve()
        for path in sorted(agent0.rglob("*")):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(base)  # raises if outside
            except (ValueError, OSError):
                continue
            rel = path.relative_to(stage_run_dir).as_posix()
            files[rel] = _sha256_of(path)
    return ArchiveManifest(files=files)


def write_manifest(stage_run_dir: Path) -> Path:
    """Atomically write ``stage_run_dir/manifest.json`` and return the path."""
    manifest = compute_manifest(stage_run_dir)
    out = stage_run_dir / "manifest.json"
    atomic_write_json(out, manifest)
    return out
