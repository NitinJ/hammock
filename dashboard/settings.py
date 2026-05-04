"""Dashboard process settings — env-driven via pydantic-settings.

Environment variables (prefix ``HAMMOCK_``):

- ``HAMMOCK_ROOT``               — hammock root directory (default: ``~/.hammock``)
- ``HAMMOCK_HOST``               — bind host (default: ``127.0.0.1``)
- ``HAMMOCK_PORT``               — bind port (default: ``8765``)
- ``HAMMOCK_FAKE_FIXTURES_DIR``  — when set, dashboard spawns drivers
                                    in fake-runner mode using fixtures
                                    from this dir; when unset, drivers
                                    use ``RealStageRunner`` (real
                                    ``claude`` subprocess)
- ``HAMMOCK_CLAUDE_BINARY``      — override the ``claude`` CLI path
                                    used by ``RealStageRunner``
                                    (default: ``claude`` from ``$PATH``)
- ``HAMMOCK_RUN_BACKGROUND_TASKS`` — start watcher / supervisor /
                                    MCP-manager background tasks in
                                    the lifespan? Default ``True``.
                                    Tests set ``False`` so they don't
                                    race the supervisor's first scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from shared import paths

RunnerMode = Literal["fake", "real"]


class Settings(BaseSettings):
    """Runtime configuration derived from environment + defaults."""

    model_config = SettingsConfigDict(env_prefix="HAMMOCK_")

    root: Path = paths.HAMMOCK_ROOT
    host: str = "127.0.0.1"
    port: int = 8765
    # Optional path to FakeStageRunner fixture directory; passed to
    # spawn_driver when set. When unset, the dashboard spawns drivers
    # in real-claude mode. Set via HAMMOCK_FAKE_FIXTURES_DIR.
    fake_fixtures_dir: Path | None = None
    # Path to the `claude` CLI used by RealStageRunner (only consulted
    # when fake_fixtures_dir is None). Defaults to `claude` from $PATH;
    # override with HAMMOCK_CLAUDE_BINARY.
    claude_binary: str = "claude"
    # Start watcher / supervisor / MCP-manager background tasks in the
    # lifespan. Default True for production. Tests that pre-seed jobs
    # would race the supervisor's first scan and pass False.
    run_background_tasks: bool = True

    @property
    def runner_mode(self) -> RunnerMode:
        """Which stage runner the dashboard will pick when spawning a
        driver. Derived from `fake_fixtures_dir` so the source of truth
        stays a single field — operators don't have to keep two flags
        in sync."""
        return "fake" if self.fake_fixtures_dir is not None else "real"
