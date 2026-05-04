"""Materialise a project's specialist catalogue for stage spawn.

Per `docs/v0-alignment-report.md` Plan #3: writes the per-stage
``agents.json`` keyed by ``agent_ref`` so the JobDriver can hand it
to ``RealStageRunner``, which passes it to claude as the
``--agents <inline json>`` flag (claude's documented mechanism for
custom agents). Returns a typed :class:`MaterialisedSpawn`.

v0 scope:
- Override-tier only (``<repo>/.hammock/agent-overrides/*.md``).
- ``settings_path`` is reserved for v1+ when the per-stage settings
  fragment lands; v0 returns the stage_run_dir itself so the field
  is always set.
"""

from __future__ import annotations

import json
from pathlib import Path

from dashboard.specialist.resolver import parse_agent_md, resolve
from shared import paths
from shared.atomic import atomic_write_text
from shared.models import ProjectConfig
from shared.models.specialist import MaterialisedSpawn, SpecialistCatalogue


def _agent_payload(project: ProjectConfig, agent_ref: str) -> dict[str, str] | None:
    """Read the override .md and return ``{description, prompt}`` for
    the claude --agents shape, or ``None`` if the file's gone."""
    repo = Path(project.repo_path)
    md_path = paths.project_agents_overrides(repo) / f"{agent_ref}.md"
    if not md_path.is_file():
        return None
    try:
        definition = parse_agent_md(agent_ref, md_path.read_text())
    except (OSError, ValueError):
        return None
    return {
        "description": definition.description,
        "prompt": definition.body.strip(),
    }


def materialise_for_spawn(
    project: ProjectConfig,
    stage_run_dir: Path,
    *,
    catalogue: SpecialistCatalogue | None = None,
) -> MaterialisedSpawn:
    """Write ``<stage_run_dir>/agents.json`` and return paths.

    ``catalogue`` is an optional pre-resolved catalogue (avoids a
    second filesystem walk if the caller already has one). When
    omitted, this function calls :func:`resolve` itself.
    """
    if catalogue is None:
        catalogue = resolve(project)

    payload: dict[str, dict[str, str]] = {}
    for entry in catalogue.agents:
        item = _agent_payload(project, entry.agent_ref)
        if item is not None:
            payload[entry.agent_ref] = item

    stage_run_dir.mkdir(parents=True, exist_ok=True)
    agents_json_path = stage_run_dir / "agents.json"
    atomic_write_text(agents_json_path, json.dumps(payload, indent=2))

    return MaterialisedSpawn(
        agents_json=str(agents_json_path),
        settings_path=str(stage_run_dir),  # reserved for v1+
    )
