"""Dashboard process settings — env-driven via pydantic-settings.

Environment variables (prefix ``HAMMOCK_``):

- ``HAMMOCK_ROOT``   — hammock root directory (default: ``~/.hammock``)
- ``HAMMOCK_HOST``   — bind host (default: ``127.0.0.1``)
- ``HAMMOCK_PORT``   — bind port (default: ``8765``)
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from shared import paths


class Settings(BaseSettings):
    """Runtime configuration derived from environment + defaults."""

    model_config = SettingsConfigDict(env_prefix="HAMMOCK_")

    root: Path = paths.HAMMOCK_ROOT
    host: str = "127.0.0.1"
    port: int = 8765
