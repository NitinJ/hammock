"""Stage 0 manual smoke.

Instantiates one of every shared/models class and prints its JSON
serialisation. If this script runs to completion, the model layer is
self-consistent: every model has a valid factory, every factory output
serialises and deserialises round-trip.

Run with::

    uv run python scripts/manual-smoke-stage0.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make `tests` importable when running from repo root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.shared.factories import (
    make_agent_def,
    make_ask_hil_item,
    make_event,
    make_job,
    make_manual_step_hil_item,
    make_presentation_block,
    make_project,
    make_review_hil_item,
    make_review_verdict,
    make_skill_def,
    make_specialist_catalogue,
    make_stage_definition,
    make_stage_run,
    make_task_record,
    make_ui_template,
)


def main() -> int:
    builders = {
        "Project": make_project,
        "JobConfig": make_job,
        "StageDefinition": make_stage_definition,
        "StageRun": make_stage_run,
        "TaskRecord": make_task_record,
        "HilItem (ask)": make_ask_hil_item,
        "HilItem (review)": make_review_hil_item,
        "HilItem (manual-step)": make_manual_step_hil_item,
        "Event": make_event,
        "ReviewVerdict": make_review_verdict,
        "PresentationBlock": make_presentation_block,
        "UiTemplate": make_ui_template,
        "AgentDef": make_agent_def,
        "SkillDef": make_skill_def,
        "SpecialistCatalogue": make_specialist_catalogue,
    }
    for label, build in builders.items():
        m = build()
        print(f"=== {label} ===")
        print(m.model_dump_json(indent=2))
        # Round-trip check
        cls = type(m)
        re = cls.model_validate_json(m.model_dump_json())
        assert re == m, f"round-trip failed for {label}"
        print()
    print(f"smoke OK: {len(builders)} model variants validated round-trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
