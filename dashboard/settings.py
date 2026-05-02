"""Dashboard process settings — env-driven, used at lifespan startup.

Stage 1 only needs ``hammock_root``. Subsequent stages will extend this
(port, host, etc.) when they land.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared import paths


@dataclass(frozen=True)
class Settings:
    """Runtime configuration derived from environment + defaults."""

    hammock_root: Path

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get("HAMMOCK_ROOT")
        root = Path(env).expanduser().resolve() if env else paths.HAMMOCK_ROOT
        return cls(hammock_root=root)
