"""Tests for ``shared.atomic``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from shared.atomic import atomic_append_jsonl, atomic_write_json, atomic_write_text


class _Sample(BaseModel):
    name: str
    count: int


def test_atomic_write_text_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "sub" / "a.txt"
    atomic_write_text(p, "hello\n")
    assert p.read_text() == "hello\n"


def test_atomic_write_text_overwrites_existing(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("old")
    atomic_write_text(p, "new")
    assert p.read_text() == "new"


def test_atomic_write_text_leaves_no_temp_files_on_success(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    atomic_write_text(p, "x")
    leftovers = [
        f for f in tmp_path.iterdir() if f.name.startswith(".") and f.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_atomic_write_json_writes_pydantic(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    atomic_write_json(p, _Sample(name="x", count=3))
    parsed = json.loads(p.read_text())
    assert parsed == {"name": "x", "count": 3}


def test_atomic_append_jsonl_appends_lines(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    atomic_append_jsonl(p, _Sample(name="a", count=1))
    atomic_append_jsonl(p, _Sample(name="b", count=2))
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"name": "a", "count": 1}
    assert json.loads(lines[1]) == {"name": "b", "count": 2}


def test_atomic_append_jsonl_rejects_too_large(tmp_path: Path) -> None:
    big = _Sample(name="x" * 5000, count=1)
    p = tmp_path / "big.jsonl"
    with pytest.raises(ValueError, match="jsonl line too large"):
        atomic_append_jsonl(p, big)
