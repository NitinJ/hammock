"""Tests for replay_since — reads events.jsonl from disk by scope.

Covers: stage scope, job scope, global scope, seq filtering, malformed
lines, missing files, and empty files.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from dashboard.state.pubsub import replay_since
from shared import paths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(offset: int = 0) -> str:
    return (datetime(2026, 5, 1, 12, 0, tzinfo=UTC)).isoformat()


def _write_event(path: Path, seq: int, event_type: str = "cost_accrued") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "seq": seq,
        "timestamp": _ts(),
        "event_type": event_type,
        "source": "agent0",
        "job_id": "job-id-1",
    }
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Stage scope
# ---------------------------------------------------------------------------


async def test_replay_stage_scope_returns_events_above_last_id(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("my-job", "my-stage", root=tmp_path)
    _write_event(path, seq=1)
    _write_event(path, seq=2)
    _write_event(path, seq=3)

    results = [e async for e in replay_since("stage:my-job:my-stage", 1, root=tmp_path)]
    assert [e.seq for e in results] == [2, 3]


async def test_replay_stage_scope_last_id_minus_one_yields_all(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("my-job", "my-stage", root=tmp_path)
    _write_event(path, seq=1)
    _write_event(path, seq=2)

    results = [e async for e in replay_since("stage:my-job:my-stage", -1, root=tmp_path)]
    assert [e.seq for e in results] == [1, 2]


async def test_replay_stage_scope_last_id_equals_max_yields_nothing(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("my-job", "my-stage", root=tmp_path)
    _write_event(path, seq=1)

    results = [e async for e in replay_since("stage:my-job:my-stage", 5, root=tmp_path)]
    assert results == []


async def test_replay_stage_scope_nonexistent_file_yields_nothing(tmp_path: Path) -> None:
    results = [e async for e in replay_since("stage:my-job:missing", 0, root=tmp_path)]
    assert results == []


async def test_replay_stage_scope_empty_file_yields_nothing(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("my-job", "my-stage", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")

    results = [e async for e in replay_since("stage:my-job:my-stage", -1, root=tmp_path)]
    assert results == []


async def test_replay_stage_scope_event_fields_parsed(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("my-job", "my-stage", root=tmp_path)
    _write_event(path, seq=7, event_type="stage_state_transition")

    results = [e async for e in replay_since("stage:my-job:my-stage", 0, root=tmp_path)]
    assert len(results) == 1
    assert results[0].seq == 7
    assert results[0].event_type == "stage_state_transition"
    assert results[0].source == "agent0"


# ---------------------------------------------------------------------------
# Job scope
# ---------------------------------------------------------------------------


async def test_replay_job_scope_reads_job_events_jsonl(tmp_path: Path) -> None:
    path = paths.job_events_jsonl("my-job", root=tmp_path)
    _write_event(path, seq=1)
    _write_event(path, seq=2)

    results = [e async for e in replay_since("job:my-job", 0, root=tmp_path)]
    assert [e.seq for e in results] == [1, 2]


async def test_replay_job_scope_nonexistent_file_yields_nothing(tmp_path: Path) -> None:
    results = [e async for e in replay_since("job:no-such-job", -1, root=tmp_path)]
    assert results == []


async def test_replay_job_scope_filters_by_seq(tmp_path: Path) -> None:
    path = paths.job_events_jsonl("my-job", root=tmp_path)
    for seq in range(1, 6):
        _write_event(path, seq=seq)

    results = [e async for e in replay_since("job:my-job", 3, root=tmp_path)]
    assert [e.seq for e in results] == [4, 5]


# ---------------------------------------------------------------------------
# Global scope
# ---------------------------------------------------------------------------


async def test_replay_global_scope_reads_all_job_directories(tmp_path: Path) -> None:
    _write_event(paths.job_events_jsonl("job-a", root=tmp_path), seq=1)
    _write_event(paths.job_events_jsonl("job-b", root=tmp_path), seq=2)

    results = [e async for e in replay_since("global", -1, root=tmp_path)]
    assert len(results) == 2


async def test_replay_global_scope_empty_jobs_dir_yields_nothing(tmp_path: Path) -> None:
    results = [e async for e in replay_since("global", -1, root=tmp_path)]
    assert results == []


async def test_replay_global_scope_missing_events_files_skipped(tmp_path: Path) -> None:
    # Create a job dir with no events.jsonl
    (paths.jobs_dir(root=tmp_path) / "job-no-events").mkdir(parents=True)
    # And one with events
    _write_event(paths.job_events_jsonl("job-has-events", root=tmp_path), seq=1)

    results = [e async for e in replay_since("global", -1, root=tmp_path)]
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Unknown / malformed scope
#
# Stage 12.5 (A1): scope validation moved to fail-fast.  The previous
# behaviour — silently yielding nothing on unknown / malformed scope —
# masked routing bugs and made deep-linked URLs look like "no events yet"
# rather than "you asked for the wrong thing."  ``replay_since`` now
# raises ``ValueError`` and the SSE route layer maps that to 422.
# ---------------------------------------------------------------------------


async def test_replay_unknown_scope_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown scope"):
        async for _ in replay_since("project:some-project", -1, root=tmp_path):
            pass


async def test_replay_stage_scope_malformed_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="malformed 'stage:' scope"):
        async for _ in replay_since("stage:no-colon-here", -1, root=tmp_path):
            pass


async def test_replay_stage_scope_empty_job_part_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="malformed 'stage:' scope"):
        async for _ in replay_since("stage::sid", -1, root=tmp_path):
            pass


async def test_replay_stage_scope_empty_stage_part_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="malformed 'stage:' scope"):
        async for _ in replay_since("stage:job:", -1, root=tmp_path):
            pass


async def test_replay_empty_scope_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown scope"):
        async for _ in replay_since("", -1, root=tmp_path):
            pass


async def test_replay_job_scope_empty_slug_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="malformed 'job:' scope"):
        async for _ in replay_since("job:", -1, root=tmp_path):
            pass


# ---------------------------------------------------------------------------
# Malformed JSONL lines
# ---------------------------------------------------------------------------


async def test_replay_skips_non_json_lines(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("j", "s", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write("not-json\n")
        f.write(
            json.dumps(
                {
                    "seq": 5,
                    "timestamp": _ts(),
                    "event_type": "cost_accrued",
                    "source": "agent0",
                    "job_id": "j1",
                }
            )
            + "\n"
        )

    results = [e async for e in replay_since("stage:j:s", -1, root=tmp_path)]
    assert [e.seq for e in results] == [5]


async def test_replay_skips_json_lines_missing_required_fields(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("j", "s", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write('{"seq": 1}\n')  # missing timestamp, event_type, source, job_id
        f.write(
            json.dumps(
                {
                    "seq": 2,
                    "timestamp": _ts(),
                    "event_type": "cost_accrued",
                    "source": "agent0",
                    "job_id": "j1",
                }
            )
            + "\n"
        )

    results = [e async for e in replay_since("stage:j:s", -1, root=tmp_path)]
    assert [e.seq for e in results] == [2]


async def test_replay_skips_blank_lines(tmp_path: Path) -> None:
    path = paths.stage_events_jsonl("j", "s", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write("\n\n")
        f.write(
            json.dumps(
                {
                    "seq": 3,
                    "timestamp": _ts(),
                    "event_type": "cost_accrued",
                    "source": "agent0",
                    "job_id": "j1",
                }
            )
            + "\n"
        )

    results = [e async for e in replay_since("stage:j:s", -1, root=tmp_path)]
    assert [e.seq for e in results] == [3]
