"""End-to-end compiler tests against the bundled templates."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from dashboard.compiler import CompileSuccess, compile_job
from shared import paths
from shared.models import JobState
from tests.dashboard.compiler.conftest import BUNDLED_TEMPLATES_DIR

_FIXED_NOW = datetime(2026, 5, 2, 12, 0, 0, tzinfo=UTC)


def _compile_default(
    *,
    hammock_root: Path,
    job_type: str = "build-feature",
    title: str = "add invite onboarding",
    request_text: str = "Build the invite-only onboarding flow.",
    dry_run: bool = False,
):
    return compile_job(
        project_slug="fake-project",
        job_type=job_type,
        title=title,
        request_text=request_text,
        root=hammock_root,
        templates_dir=BUNDLED_TEMPLATES_DIR,
        dry_run=dry_run,
        now=_FIXED_NOW,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_build_feature_compiles_cleanly(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, CompileSuccess), res
    assert res.job_slug == "2026-05-02-add-invite-onboarding"
    assert res.job_config.state is JobState.SUBMITTED
    assert res.job_config.job_type == "build-feature"
    assert len(res.stages) >= 12
    # Verify required artifacts on disk
    assert paths.job_json(res.job_slug, root=hammock_root).exists()
    assert paths.job_prompt(res.job_slug, root=hammock_root).exists()
    assert paths.job_stage_list(res.job_slug, root=hammock_root).exists()


def test_fix_bug_compiles_cleanly(hammock_root: Path, fake_project) -> None:
    res = _compile_default(
        hammock_root=hammock_root,
        job_type="fix-bug",
        title="login redirect loop",
    )
    assert isinstance(res, CompileSuccess), res
    assert res.job_config.job_type == "fix-bug"
    # First stage of fix-bug is write-bug-report
    assert res.stages[0].id == "write-bug-report"


def test_compile_writes_job_json_with_submitted_state(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, CompileSuccess)
    data = json.loads(paths.job_json(res.job_slug, root=hammock_root).read_text())
    assert data["state"] == "SUBMITTED"
    assert data["job_slug"] == res.job_slug
    assert data["project_slug"] == "fake-project"


def test_compile_writes_prompt_md_verbatim(hammock_root: Path, fake_project) -> None:
    prompt = "Build a thing.\nWith newlines.\n"
    res = compile_job(
        project_slug="fake-project",
        job_type="build-feature",
        title="thing",
        request_text=prompt,
        root=hammock_root,
        templates_dir=BUNDLED_TEMPLATES_DIR,
        now=_FIXED_NOW,
    )
    assert isinstance(res, CompileSuccess)
    assert paths.job_prompt(res.job_slug, root=hammock_root).read_text() == prompt


def test_compile_writes_parseable_stage_list_yaml(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, CompileSuccess)
    parsed = yaml.safe_load(paths.job_stage_list(res.job_slug, root=hammock_root).read_text())
    assert isinstance(parsed, dict)
    assert "stages" in parsed
    assert isinstance(parsed["stages"], list)


def test_compile_persists_loop_back_max_iterations(hammock_root: Path, fake_project) -> None:
    """Stage 12.5 (E2): loop_back.max_iterations must round-trip from template
    YAML through compile to compiled stage-list.yaml.  build-feature has six
    loop_back stages; each must keep its declared max_iterations.
    """
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, CompileSuccess)

    # Inspect the compiled in-memory representation
    looping = [s for s in res.stages if s.loop_back is not None]
    assert len(looping) >= 1, "build-feature template should have loop_back stages"
    for s in looping:
        assert s.loop_back is not None  # narrow for type checker
        assert s.loop_back.max_iterations >= 1
        assert s.loop_back.condition  # non-empty
        assert s.loop_back.to  # non-empty target
        assert s.loop_back.on_exhaustion is not None

    # And in the persisted YAML — the round-trip the Job Driver actually reads
    parsed = yaml.safe_load(paths.job_stage_list(res.job_slug, root=hammock_root).read_text())
    yaml_looping = [s for s in parsed["stages"] if s.get("loop_back")]
    assert len(yaml_looping) == len(looping)
    for s in yaml_looping:
        lb = s["loop_back"]
        assert isinstance(lb["max_iterations"], int)
        assert lb["max_iterations"] >= 1
        assert "condition" in lb
        assert "to" in lb
        assert "on_exhaustion" in lb


def test_param_binding_substitutes_job_title(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root, title="my new feature")
    assert isinstance(res, CompileSuccess)
    parsed = yaml.safe_load(paths.job_stage_list(res.job_slug, root=hammock_root).read_text())
    # Find a stage with a presentation summary (review-design-spec-human)
    summaries = [
        s.get("presentation", {}).get("summary", "")
        for s in parsed["stages"]
        if s.get("presentation")
    ]
    assert any("my new feature" in s for s in summaries)


def test_dry_run_does_not_write(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root, dry_run=True)
    assert isinstance(res, CompileSuccess)
    assert res.dry_run
    assert not res.job_dir.exists()


def test_slug_collision_appends_suffix(hammock_root: Path, fake_project) -> None:
    res1 = _compile_default(hammock_root=hammock_root, title="same title")
    assert isinstance(res1, CompileSuccess)
    res2 = _compile_default(hammock_root=hammock_root, title="same title")
    assert isinstance(res2, CompileSuccess)
    assert res1.job_slug != res2.job_slug
    assert res2.job_slug.endswith("-2")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_project_fails(hammock_root: Path) -> None:
    res = compile_job(
        project_slug="no-such-project",
        job_type="build-feature",
        title="x",
        request_text="x",
        root=hammock_root,
        templates_dir=BUNDLED_TEMPLATES_DIR,
        now=_FIXED_NOW,
    )
    assert isinstance(res, list)
    assert any(f.kind == "project_not_found" for f in res)


def test_unknown_job_type_fails(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root, job_type="totally-fake")
    assert isinstance(res, list)
    assert any(f.kind == "template_not_found" for f in res)


def test_empty_title_fails_slug_derivation(hammock_root: Path, fake_project) -> None:
    res = _compile_default(hammock_root=hammock_root, title="!!!!")
    assert isinstance(res, list)
    assert any(f.kind == "param_binding" for f in res)


# ---------------------------------------------------------------------------
# Override semantics
# ---------------------------------------------------------------------------


def _write_override(fake_project, job_type: str, override_yaml: str) -> Path:
    """Write a per-project override at the canonical path."""
    repo = Path(fake_project.repo_path)
    overrides_dir = repo / ".hammock" / "job-template-overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    p = overrides_dir / f"{job_type}.yaml"
    p.write_text(override_yaml)
    return p


def test_override_modifies_existing_stage_field(hammock_root: Path, fake_project) -> None:
    """Modifying a budget on an existing stage is permitted."""
    _write_override(
        fake_project,
        "build-feature",
        """\
stages:
  - id: write-design-spec
    budget:
      max_turns: 999
""",
    )
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, CompileSuccess), res
    write_design = next(s for s in res.stages if s.id == "write-design-spec")
    assert write_design.budget.max_turns == 999


def test_override_rejects_unknown_stage_id(hammock_root: Path, fake_project) -> None:
    _write_override(
        fake_project,
        "build-feature",
        """\
stages:
  - id: brand-new-stage
    description: this should be rejected
    worker: agent
    agent_ref: x
    budget: { max_turns: 1 }
    exit_condition: {}
""",
    )
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, list)
    assert any("override" in f.kind for f in res)
    assert any("unknown stage id" in f.message.lower() for f in res)


def test_override_rejects_reorder(hammock_root: Path, fake_project) -> None:
    """An override that places stages out of order vs base is rejected."""
    _write_override(
        fake_project,
        "build-feature",
        """\
stages:
  - id: write-design-spec
    budget: { max_turns: 1 }
  - id: write-problem-spec
    budget: { max_turns: 1 }
""",
    )
    res = _compile_default(hammock_root=hammock_root)
    assert isinstance(res, list)
    assert any("override:reorder" == f.kind for f in res), res


def test_override_modify_only_summary_text(hammock_root: Path, fake_project) -> None:
    """Modifying a presentation.summary text is permitted."""
    _write_override(
        fake_project,
        "build-feature",
        """\
stages:
  - id: review-design-spec-human
    presentation:
      summary: "[CUSTOM] design-spec ready for ${job.title}"
""",
    )
    res = _compile_default(hammock_root=hammock_root, title="custom title")
    assert isinstance(res, CompileSuccess)
    parsed = yaml.safe_load(paths.job_stage_list(res.job_slug, root=hammock_root).read_text())
    review_stage = next(s for s in parsed["stages"] if s["id"] == "review-design-spec-human")
    assert "[CUSTOM]" in review_stage["presentation"]["summary"]
    assert "custom title" in review_stage["presentation"]["summary"]
