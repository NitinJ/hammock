"""Dashboard v2 settings — kept tiny."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hammock_v2.engine.paths import DEFAULT_ROOT, resolve_root


@dataclass
class AppSettings:
    root: Path
    project_repo_path: Path | None
    claude_binary: str
    runner_mode: str  # "real" or "fake"
    static_dist: Path  # path to dashboard_v2/frontend/dist


def load_settings() -> AppSettings:
    root = resolve_root(Path(os.environ["HAMMOCK_V2_ROOT"]) if "HAMMOCK_V2_ROOT" in os.environ else None)
    project = os.environ.get("HAMMOCK_V2_PROJECT_REPO_PATH")
    project_path = Path(project) if project else None
    claude_binary = os.environ.get("HAMMOCK_V2_CLAUDE_BINARY", "claude")
    runner_mode = os.environ.get("HAMMOCK_V2_RUNNER_MODE", "real")
    here = Path(__file__).resolve().parent
    static_dist = here / "frontend" / "dist"
    return AppSettings(
        root=root,
        project_repo_path=project_path,
        claude_binary=claude_binary,
        runner_mode=runner_mode,
        static_dist=static_dist,
    )


__all__ = ["AppSettings", "DEFAULT_ROOT", "load_settings"]
