"""Pytest config for e2e_v1 — registers the opt-in marker."""

from __future__ import annotations


def pytest_configure(config: object) -> None:
    config.addinivalue_line(  # type: ignore[attr-defined]
        "markers",
        "real_claude_v1: opt-in e2e test against real Claude + real GitHub. "
        "Requires HAMMOCK_E2E_REAL_CLAUDE=1.",
    )
