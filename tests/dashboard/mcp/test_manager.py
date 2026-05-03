"""MCPManager — spawn/dispose lifecycle and config emission."""

from __future__ import annotations

import sys
from pathlib import Path

from dashboard.mcp.channel import Channel
from dashboard.mcp.manager import MCPManager, ServerHandle


def test_spawn_returns_handle_with_channel(hammock_root: Path) -> None:
    mgr = MCPManager()
    handle = mgr.spawn(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)

    assert isinstance(handle, ServerHandle)
    assert handle.job_slug == "proj/feat"
    assert handle.stage_id == "implement-1"
    assert handle.root == hammock_root
    assert isinstance(handle.channel, Channel)


def test_spawn_emits_mcp_config_for_claude(hammock_root: Path) -> None:
    """The mcp_config has the launch command for Claude Code's stdio MCP."""
    mgr = MCPManager()
    handle = mgr.spawn(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)

    cfg = handle.mcp_config
    assert "mcpServers" in cfg
    servers = cfg["mcpServers"]
    assert "hammock-dashboard" in servers
    server = servers["hammock-dashboard"]

    # The launch command must be ``python -m dashboard.mcp <job> <stage> --root <root>``
    assert server["command"] == sys.executable
    args = server["args"]
    assert args[0] == "-m"
    assert args[1] == "dashboard.mcp"
    assert "proj/feat" in args
    assert "implement-1" in args
    assert "--root" in args
    assert str(hammock_root) in args


def test_spawn_uses_custom_python_executable(hammock_root: Path) -> None:
    mgr = MCPManager(python_executable="/opt/python/3.12/bin/python")
    handle = mgr.spawn(job_slug="p/f", stage_id="s1", root=hammock_root)
    assert handle.mcp_config["mcpServers"]["hammock-dashboard"]["command"] == (
        "/opt/python/3.12/bin/python"
    )


def test_dispose_is_idempotent(hammock_root: Path) -> None:
    mgr = MCPManager()
    handle = mgr.spawn(job_slug="p/f", stage_id="s1", root=hammock_root)
    mgr.dispose(handle)
    mgr.dispose(handle)  # second dispose must not raise


def test_spawn_two_stages_distinct_handles(hammock_root: Path) -> None:
    mgr = MCPManager()
    h1 = mgr.spawn(job_slug="p/f", stage_id="s1", root=hammock_root)
    h2 = mgr.spawn(job_slug="p/f", stage_id="s2", root=hammock_root)

    assert h1 is not h2
    assert h1.stage_id != h2.stage_id
    assert h1.channel is not h2.channel
