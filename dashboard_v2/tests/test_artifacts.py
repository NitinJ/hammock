"""Unit tests for artifact filename sanitization + saving."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard_v2.api.artifacts import sanitize_filename, save_artifacts
from hammock_v2.engine import paths


def test_sanitize_simple() -> None:
    assert sanitize_filename("design.md") == "design.md"


def test_sanitize_strips_path_separators() -> None:
    assert sanitize_filename("/etc/passwd") == "passwd"
    assert sanitize_filename("../../etc/shadow") == "shadow"
    assert sanitize_filename("a\\b\\c.txt") == "c.txt"


def test_sanitize_replaces_unsafe_chars() -> None:
    name = sanitize_filename("hello world!@#.txt")
    assert " " not in name
    assert "!" not in name
    assert name.endswith(".txt")


def test_sanitize_strips_leading_dots() -> None:
    assert sanitize_filename(".hidden") == "hidden"
    assert sanitize_filename("...weird.txt") == "weird.txt"


def test_sanitize_caps_length() -> None:
    long_name = "a" * 500 + ".txt"
    out = sanitize_filename(long_name)
    assert len(out) <= 255
    assert out.endswith(".txt")


def test_sanitize_empty_falls_back() -> None:
    assert sanitize_filename("") == "artifact"
    assert sanitize_filename("///") == "artifact"
    assert sanitize_filename("...") == "artifact"


def test_sanitize_strips_control_chars() -> None:
    name = sanitize_filename("foo\x00bar\x07.txt")
    assert "\x00" not in name
    assert "\x07" not in name


def test_save_artifacts_writes_files(tmp_path: Path) -> None:
    paths.ensure_job_layout("s1", root=tmp_path)
    saved = save_artifacts(
        slug="s1",
        files=[("a.txt", b"hi"), ("b.log", b"world")],
        root=tmp_path,
    )
    assert len(saved) == 2
    inputs = paths.inputs_dir("s1", root=tmp_path)
    assert (inputs / "a.txt").read_bytes() == b"hi"
    assert (inputs / "b.log").read_bytes() == b"world"


def test_save_artifacts_disambiguates_collisions(tmp_path: Path) -> None:
    paths.ensure_job_layout("s2", root=tmp_path)
    save_artifacts(
        slug="s2",
        files=[("dup.txt", b"first"), ("dup.txt", b"second"), ("dup.txt", b"third")],
        root=tmp_path,
    )
    files = sorted(p.name for p in paths.inputs_dir("s2", root=tmp_path).iterdir())
    assert files == ["dup-1.txt", "dup-2.txt", "dup.txt"]


def test_save_artifacts_rejects_oversize(tmp_path: Path) -> None:
    paths.ensure_job_layout("s3", root=tmp_path)
    huge = b"a" * (51 * 1024 * 1024)  # 51 MB > 50 MB cap
    with pytest.raises(ValueError, match="exceeds cap"):
        save_artifacts(slug="s3", files=[("big.bin", huge)], root=tmp_path)


def test_save_artifacts_empty_list_is_noop(tmp_path: Path) -> None:
    paths.ensure_job_layout("s4", root=tmp_path)
    saved = save_artifacts(slug="s4", files=[], root=tmp_path)
    assert saved == []
    # No inputs dir created (no files to save)
    inputs = paths.inputs_dir("s4", root=tmp_path)
    assert not inputs.is_dir() or not list(inputs.iterdir())


def test_save_artifacts_path_traversal_attempt(tmp_path: Path) -> None:
    """A malicious filename can't escape the inputs/ dir."""
    paths.ensure_job_layout("s5", root=tmp_path)
    save_artifacts(
        slug="s5",
        files=[("../../etc/passwd", b"root:x:0:0")],
        root=tmp_path,
    )
    # The file landed inside inputs/ as `passwd` (or similar safe leaf)
    inputs = paths.inputs_dir("s5", root=tmp_path)
    files = list(inputs.iterdir())
    assert len(files) == 1
    saved = files[0]
    # Resolve and check it's inside inputs
    assert inputs.resolve() in saved.resolve().parents
    # No escape
    assert (tmp_path / "etc" / "passwd").exists() is False
