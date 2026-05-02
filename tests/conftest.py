"""Shared pytest fixtures for hammock tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def hammock_root(tmp_path: Path) -> Iterator[Path]:
    """A clean ``hammock_root`` rooted at ``tmp_path``.

    Tests that exercise path helpers can pass this as ``root=...``. Tests that
    monkey-patch ``HAMMOCK_ROOT`` should use ``monkeypatch.setattr`` against
    ``shared.paths.HAMMOCK_ROOT`` directly.
    """
    root = tmp_path / "hammock-root"
    root.mkdir()
    yield root
