"""build_server tests — wires the four tools into a FastMCP instance."""

from __future__ import annotations

from pathlib import Path

from dashboard.mcp.server import build_server


def test_build_server_registers_four_tools(hammock_root: Path) -> None:
    server = build_server(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)

    # FastMCP exposes ``list_tools`` (async); we use the underlying registry.
    # The ``_tool_manager`` is the documented internal store; the public
    # API is async, but tests need a sync inspection point.
    names = sorted(server._tool_manager._tools.keys())  # type: ignore[attr-defined]
    assert names == ["append_stages", "open_ask", "open_task", "update_task"]


def test_build_server_name_and_meta(hammock_root: Path) -> None:
    server = build_server(job_slug="p/f", stage_id="s1", root=hammock_root)
    assert server.name == "hammock-dashboard"
