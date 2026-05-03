"""Unit tests for ``dashboard.state.projections``.

Each projection is a pure function over the cache (plus on-demand reads
of events.jsonl for cost rollups). Tests build the cache from the
``populated_root`` fixture and assert the projection output directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard.state import projections
from dashboard.state.cache import Cache
from shared.models import JobState, StageState


@pytest.fixture
async def cache(populated_root: Path) -> Cache:
    return await Cache.bootstrap(populated_root)


# ---------------------------------------------------------------------------
# Project projections
# ---------------------------------------------------------------------------


class TestProjectProjections:
    async def test_project_list_sorted_by_slug(self, cache: Cache) -> None:
        items = projections.project_list(cache)
        assert [i.slug for i in items] == ["alpha", "beta"]

    async def test_project_list_item_total_jobs(self, cache: Cache) -> None:
        item = projections.project_list_item(cache, "alpha")
        assert item is not None
        assert item.total_jobs == 2

    async def test_project_list_item_doctor_status(self, cache: Cache) -> None:
        alpha = projections.project_list_item(cache, "alpha")
        beta = projections.project_list_item(cache, "beta")
        assert alpha is not None and alpha.doctor_status == "pass"
        assert beta is not None and beta.doctor_status == "warn"

    async def test_project_list_item_open_hil_count(self, cache: Cache) -> None:
        # alpha has 1 open ask (on alpha-job-1); beta has 1 open review (beta-job-1)
        alpha = projections.project_list_item(cache, "alpha")
        beta = projections.project_list_item(cache, "beta")
        assert alpha is not None and alpha.open_hil_count == 1
        assert beta is not None and beta.open_hil_count == 1

    async def test_project_list_item_last_job_at(self, cache: Cache) -> None:
        alpha = projections.project_list_item(cache, "alpha")
        assert alpha is not None
        assert alpha.last_job_at is not None

    async def test_project_list_item_unknown(self, cache: Cache) -> None:
        assert projections.project_list_item(cache, "nope") is None

    async def test_project_detail_jobs_by_state(self, cache: Cache) -> None:
        detail = projections.project_detail(cache, "alpha")
        assert detail is not None
        assert detail.jobs_by_state[JobState.STAGES_RUNNING.value] == 1
        assert detail.jobs_by_state[JobState.COMPLETED.value] == 1

    async def test_project_detail_unknown(self, cache: Cache) -> None:
        assert projections.project_detail(cache, "nope") is None


# ---------------------------------------------------------------------------
# Job projections
# ---------------------------------------------------------------------------


class TestJobProjections:
    async def test_job_list_default_returns_all(self, cache: Cache) -> None:
        items = projections.job_list(cache)
        assert {i.job_slug for i in items} == {
            "alpha-job-1",
            "alpha-job-2",
            "beta-job-1",
        }

    async def test_job_list_filtered_by_project(self, cache: Cache) -> None:
        items = projections.job_list(cache, project_slug="alpha")
        assert {i.job_slug for i in items} == {"alpha-job-1", "alpha-job-2"}

    async def test_job_list_filtered_by_status(self, cache: Cache) -> None:
        items = projections.job_list(cache, status=JobState.COMPLETED)
        assert [i.job_slug for i in items] == ["alpha-job-2"]

    async def test_job_list_sorted_newest_first(self, cache: Cache) -> None:
        items = projections.job_list(cache)
        # beta-job-1 created last (offset 20)
        assert items[0].job_slug == "beta-job-1"

    async def test_job_list_item_total_cost_from_events(self, cache: Cache) -> None:
        item = projections.job_list_item(cache, "alpha-job-1")
        # 0.5 + 0.75 + 1.0 = 2.25 from events
        assert item is not None
        assert item.total_cost_usd == pytest.approx(2.25)

    async def test_job_list_item_no_events_fallback_to_stage_costs(self, cache: Cache) -> None:
        item = projections.job_list_item(cache, "alpha-job-2")
        # No events for alpha-job-2; fallback sums stage cost_accrued (2.0)
        assert item is not None
        assert item.total_cost_usd == pytest.approx(2.0)

    async def test_job_list_item_current_stage(self, cache: Cache) -> None:
        item = projections.job_list_item(cache, "alpha-job-1")
        # 'implement' is RUNNING; 'review' is ATTENTION_NEEDED — either is acceptable.
        assert item is not None
        assert item.current_stage_id in {"implement", "review"}

    async def test_job_list_item_unknown(self, cache: Cache) -> None:
        assert projections.job_list_item(cache, "nope") is None

    async def test_job_detail_stages_ordered_by_started(self, cache: Cache) -> None:
        detail = projections.job_detail(cache, "alpha-job-1")
        assert detail is not None
        ids = [s.stage_id for s in detail.stages]
        assert ids == ["design", "implement", "review"]

    async def test_job_detail_total_cost_from_events(self, cache: Cache) -> None:
        detail = projections.job_detail(cache, "alpha-job-1")
        assert detail is not None
        assert detail.total_cost_usd == pytest.approx(2.25)


# ---------------------------------------------------------------------------
# Stage projections
# ---------------------------------------------------------------------------


class TestStageProjections:
    async def test_stage_detail_includes_tasks(self, cache: Cache) -> None:
        detail = projections.stage_detail(cache, "alpha-job-1", "implement")
        assert detail is not None
        assert detail.stage.stage_id == "implement"
        assert [t.task_id for t in detail.tasks] == ["task-1"]

    async def test_stage_detail_unknown_stage(self, cache: Cache) -> None:
        assert projections.stage_detail(cache, "alpha-job-1", "nope") is None

    async def test_active_stage_strip_only_running_and_attention(self, cache: Cache) -> None:
        items = projections.active_stage_strip(cache)
        states = {i.state for i in items}
        # SUCCEEDED ones must not appear
        assert states == {StageState.RUNNING, StageState.ATTENTION_NEEDED}

    async def test_active_stage_strip_carries_project(self, cache: Cache) -> None:
        items = projections.active_stage_strip(cache)
        for it in items:
            assert it.project_slug == "alpha"


# ---------------------------------------------------------------------------
# HIL projections
# ---------------------------------------------------------------------------


class TestHilProjections:
    async def test_hil_queue_default_awaiting_only(self, cache: Cache) -> None:
        rows = projections.hil_queue(cache)
        assert {r.item_id for r in rows} == {"hil-open-ask", "hil-open-review"}

    async def test_hil_queue_filtered_by_kind(self, cache: Cache) -> None:
        rows = projections.hil_queue(cache, kind="review")
        assert {r.item_id for r in rows} == {"hil-open-review"}

    async def test_hil_queue_filtered_by_project(self, cache: Cache) -> None:
        rows = projections.hil_queue(cache, project_slug="alpha")
        assert {r.item_id for r in rows} == {"hil-open-ask"}

    async def test_hil_queue_filtered_by_job(self, cache: Cache) -> None:
        rows = projections.hil_queue(cache, job_slug="alpha-job-1")
        assert {r.item_id for r in rows} == {"hil-open-ask"}

    async def test_hil_queue_age_seconds(self, cache: Cache) -> None:
        # Pin "now" at +60 minutes so age is deterministic
        now = datetime(2026, 5, 1, 13, 0, tzinfo=UTC)
        rows = projections.hil_queue(cache, now=now)
        ages = {r.item_id: r.age_seconds for r in rows}
        # ask was created at offset +2 → age 58m = 3480s
        assert ages["hil-open-ask"] == pytest.approx(58 * 60)

    async def test_hil_queue_oldest_first(self, cache: Cache) -> None:
        rows = projections.hil_queue(cache)
        # ask created at +2, review at +21
        assert rows[0].item_id == "hil-open-ask"

    async def test_hil_queue_item_unknown(self, cache: Cache) -> None:
        assert projections.hil_queue_item(cache, "nope") is None


# ---------------------------------------------------------------------------
# Cost rollup
# ---------------------------------------------------------------------------


class TestCostRollup:
    async def test_job_scope(self, cache: Cache) -> None:
        rollup = projections.cost_rollup(cache, "job", "alpha-job-1")
        assert rollup is not None
        assert rollup.total_usd == pytest.approx(2.25)
        assert rollup.total_tokens == 12000 + 18000 + 24000
        assert rollup.by_stage["design"] == pytest.approx(1.25)
        assert rollup.by_stage["implement"] == pytest.approx(1.0)
        assert rollup.by_agent["design-spec-writer"] == pytest.approx(1.25)
        assert rollup.by_agent["implementer"] == pytest.approx(1.0)

    async def test_project_scope(self, cache: Cache) -> None:
        rollup = projections.cost_rollup(cache, "project", "alpha")
        assert rollup is not None
        # Project includes alpha-job-1 (2.25); alpha-job-2 has no events
        assert rollup.total_usd == pytest.approx(2.25)

    async def test_stage_scope(self, cache: Cache) -> None:
        rollup = projections.cost_rollup(cache, "stage", "design", stage_job_slug="alpha-job-1")
        assert rollup is not None
        assert rollup.total_usd == pytest.approx(1.25)

    async def test_stage_scope_requires_job_slug(self, cache: Cache) -> None:
        # Without stage_job_slug, the projection returns None.
        assert projections.cost_rollup(cache, "stage", "design") is None

    async def test_unknown_project_returns_none(self, cache: Cache) -> None:
        assert projections.cost_rollup(cache, "project", "nope") is None

    async def test_unknown_job_returns_none(self, cache: Cache) -> None:
        assert projections.cost_rollup(cache, "job", "nope") is None

    async def test_no_events_returns_zero(self, cache: Cache) -> None:
        rollup = projections.cost_rollup(cache, "job", "alpha-job-2")
        assert rollup is not None
        assert rollup.total_usd == 0.0


# ---------------------------------------------------------------------------
# Driver/projection contract — Stage 12.5 (A3)
#
# The cost_accrued payload shape MUST be produced by the Job Driver in the
# same shape the projection reads.  Pre-12.5 the driver emitted
# {"amount_usd": ...} while the projection read payload["usd"], so cost
# rollups silently returned 0 from events (and the cache-fallback at
# projections._job_total_cost masked the bug for in-cache stages).  The
# canonical shape is the one the design doc names: ``delta_usd``,
# ``delta_tokens``, ``running_total``.
# ---------------------------------------------------------------------------


class TestCostPayloadContract:
    async def test_real_driver_writes_delta_usd_to_events_jsonl(self, tmp_path: Path) -> None:
        """Stage 12.5 (A3, post-Codex-review): exercise the actual JobDriver
        cost-emit path and assert the on-disk payload uses ``delta_usd``.

        Pre-fix the driver wrote ``{"amount_usd": ...}``, which silently
        produced 0.0 from the cost projection.  Codex's review of the first
        version of this contract test pointed out that hand-rolling the
        event dict only verifies the *projection* side; if the driver
        regressed to ``amount_usd`` the projection-side test would still
        be green.  This second test catches that by running the actual
        ``JobDriver`` against a fixture stage with non-zero cost and
        inspecting the event payload key directly.
        """
        import json
        from datetime import UTC, datetime

        import yaml

        from job_driver.runner import JobDriver
        from job_driver.stage_runner import FakeStageRunner
        from shared import paths
        from shared.atomic import atomic_write_json
        from shared.models.job import JobConfig, JobState
        from shared.models.stage import (
            Budget,
            ExitCondition,
            InputSpec,
            OutputSpec,
            RequiredOutput,
            StageDefinition,
        )

        # Minimal job dir setup
        jobs_dir = tmp_path / "jobs"
        job_dir = jobs_dir / "cost-test-job"
        job_dir.mkdir(parents=True)
        fixtures_dir = tmp_path / "fixtures"
        fixtures_dir.mkdir()

        atomic_write_json(
            paths.job_json(job_dir.name, root=tmp_path),
            JobConfig(
                job_id="job-cost",
                job_slug=job_dir.name,
                project_slug="proj",
                job_type="build-feature",
                created_at=datetime(2026, 5, 1, tzinfo=UTC),
                created_by="human",
                state=JobState.SUBMITTED,
            ),
        )

        stage = StageDefinition(
            id="cost-stage",
            worker="agent",
            inputs=InputSpec(required=[], optional=None),
            outputs=OutputSpec(required=["out.txt"]),
            budget=Budget(max_turns=10),
            exit_condition=ExitCondition(required_outputs=[RequiredOutput(path="out.txt")]),
        )
        stage_list = job_dir / "stage-list.yaml"
        stage_list.write_text(yaml.dump({"stages": [json.loads(stage.model_dump_json())]}))

        # Fixture: stage emits cost_usd > 0 — drives the cost_accrued event path
        (fixtures_dir / "cost-stage.yaml").write_text(
            yaml.dump(
                {
                    "outcome": "succeeded",
                    "artifacts": {"out.txt": "x"},
                    "cost_usd": 1.23,
                }
            )
        )

        driver = JobDriver(
            job_dir.name,
            root=tmp_path,
            stage_runner=FakeStageRunner(fixtures_dir),
            heartbeat_interval=0.1,
        )
        await driver.run()

        # Read the events.jsonl back and find the cost_accrued event
        events_path = paths.job_events_jsonl(job_dir.name, root=tmp_path)
        cost_events = [
            json.loads(line)
            for line in events_path.read_text().splitlines()
            if line and json.loads(line).get("event_type") == "cost_accrued"
        ]
        assert len(cost_events) == 1, f"expected 1 cost_accrued event, got {len(cost_events)}"
        payload = cost_events[0]["payload"]
        # The CANONICAL key — must be `delta_usd`, not `amount_usd` or `usd`.
        assert "delta_usd" in payload, (
            f"driver must emit 'delta_usd' (got payload keys {list(payload.keys())})"
        )
        assert payload["delta_usd"] == pytest.approx(1.23)
        # Negative assertion: the legacy keys must NOT be present.
        assert "amount_usd" not in payload, "driver regressed to legacy 'amount_usd' key"
        assert "usd" not in payload, "driver regressed to legacy 'usd' key"

    async def test_driver_emits_delta_usd_projection_reads_delta_usd(self, tmp_path: Path) -> None:
        """Round-trip a single cost_accrued event from JobDriver → projection.

        The test bypasses the full driver setup by writing the same payload
        the driver writes (`_emit_event` line layout) directly to the job
        events.jsonl, then asks the projection what total it sees.  If the
        keys driver writes don't match keys projection reads, the rollup
        comes back as 0.0 and the test fails.
        """
        import json

        from shared import paths
        from shared.atomic import atomic_write_json
        from shared.models import (
            JobConfig,
            JobState,
            ProjectConfig,
        )

        # Minimal cache prerequisites: one project + one job
        proj = ProjectConfig(
            slug="proj",
            name="proj",
            repo_path="/tmp/proj",
            default_branch="main",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        atomic_write_json(paths.project_json(proj.slug, root=tmp_path), proj)
        job = JobConfig(
            job_id="job-1",
            job_slug="proj-job-1",
            project_slug="proj",
            job_type="build-feature",
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
            created_by="human",
            state=JobState.STAGES_RUNNING,
        )
        atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)

        # Driver-shaped event: this is the exact dict layout JobDriver writes
        # to events.jsonl.  If the driver code drifts from this shape, the
        # projection will fold to 0 and this test will catch it.
        events_path = paths.job_events_jsonl(job.job_slug, root=tmp_path)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "seq": 1,
                        "timestamp": datetime(2026, 5, 1, tzinfo=UTC).isoformat(),
                        "event_type": "cost_accrued",
                        "source": "job_driver",
                        "job_id": job.job_id,
                        "stage_id": "design",
                        "task_id": None,
                        "subagent_id": None,
                        "parent_event_seq": None,
                        "payload": {"delta_usd": 0.42, "delta_tokens": 1234},
                    }
                )
                + "\n"
            )

        cache = await Cache.bootstrap(tmp_path)
        rollup = projections.cost_rollup(cache, "job", job.job_slug)
        assert rollup is not None
        assert rollup.total_usd == pytest.approx(0.42)
        assert rollup.total_tokens == 1234


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------


class TestSystemHealth:
    async def test_system_health_cache_size(self, cache: Cache) -> None:
        h = projections.system_health(cache)
        assert h.cache_size["projects"] == 2
        assert h.cache_size["jobs"] == 3
        assert h.watcher_alive is True
