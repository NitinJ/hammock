"""Stage 1 manual smoke.

End-to-end exercise of the cache + watcher + pub/sub against a real
filesystem and a real ``watchfiles.awatch`` watcher. Run with::

    uv run python scripts/manual-smoke-stage1.py

What it does:

1. Creates a temporary hammock root and seeds it with a project + a job +
   a stage + an HIL item.
2. Bootstraps a Cache; asserts every entity is there.
3. Spawns the watcher in a background task with a real ``watchfiles.awatch``.
4. Subscribes to ``global``, ``job:<slug>``, and ``stage:<job>:<sid>``.
5. Mutates files (modify, delete, add) and asserts each subscriber sees
   the corresponding change. Times out at 5s per assertion.
6. Reports timing and the final cache size.

Exits 0 on success.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.state.cache import Cache, ChangeKind
from dashboard.state.pubsub import InProcessPubSub
from dashboard.watcher.tailer import CacheChange
from dashboard.watcher.tailer import run as run_watcher
from shared import paths
from shared.atomic import atomic_write_json
from tests.shared.factories import (
    make_ask_hil_item,
    make_job,
    make_project,
    make_stage_run,
)

WAIT_TIMEOUT = 5.0


async def _seed(root: Path) -> dict[str, object]:
    project = make_project()
    job = make_job()
    stage = make_stage_run()
    hil = make_ask_hil_item()

    atomic_write_json(paths.project_json(project.slug, root=root), project)
    atomic_write_json(paths.job_json(job.job_slug, root=root), job)
    atomic_write_json(paths.stage_json(job.job_slug, stage.stage_id, root=root), stage)
    atomic_write_json(paths.hil_item_path(job.job_slug, hil.id, root=root), hil)
    return {"project": project, "job": job, "stage": stage, "hil": hil}


async def _expect(sub_aiter, predicate_msg: str) -> CacheChange:
    msg = await asyncio.wait_for(anext(sub_aiter), timeout=WAIT_TIMEOUT)
    print(f"  ✓ saw: {predicate_msg} ({msg.kind} {msg.path.name})")
    return msg


async def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hammock-smoke-") as tmp:
        root = Path(tmp)
        print(f"smoke root: {root}")

        seeds = await _seed(root)
        project = seeds["project"]
        job = seeds["job"]
        stage = seeds["stage"]

        # 1. Bootstrap and assert seeds are visible.
        t0 = time.monotonic()
        cache = await Cache.bootstrap(root)
        boot_ms = (time.monotonic() - t0) * 1000
        sizes = cache.size()
        print(f"bootstrap: {boot_ms:.1f}ms — {sizes}")
        assert sizes == {"projects": 1, "jobs": 1, "stages": 1, "hil": 1}

        # 2. Run the watcher in the background.
        bus: InProcessPubSub[CacheChange] = InProcessPubSub()
        sub_global = bus.subscribe("global")
        sub_job = bus.subscribe(f"job:{job.job_slug}")
        sub_stage = bus.subscribe(f"stage:{job.job_slug}:{stage.stage_id}")

        stop_event = asyncio.Event()
        watcher_task = asyncio.create_task(run_watcher(cache, bus, stop_event=stop_event))

        # Give watchfiles a moment to begin watching. WSL2 inotify has a
        # cold-start latency; 1s is comfortable.
        await asyncio.sleep(1.0)

        # 3. Modify the project. Expect global + project scopes to fire.
        renamed = project.model_copy(update={"name": "renamed"})
        atomic_write_json(paths.project_json(project.slug, root=root), renamed)
        await _expect(sub_global, "project modified arrives on global")

        # 4. Touch the stage file (modify state to SUCCEEDED).
        from shared.models import StageState

        stage_done = stage.model_copy(update={"state": StageState.SUCCEEDED})
        atomic_write_json(paths.stage_json(job.job_slug, stage.stage_id, root=root), stage_done)
        await _expect(sub_stage, "stage modified arrives on stage scope")
        await _expect(sub_job, "stage modified also fires job scope")

        # 5. Delete the HIL item.
        hil_path = paths.hil_item_path(
            job.job_slug,
            seeds["hil"].id,
            root=root,  # type: ignore[attr-defined]
        )
        hil_path.unlink()
        # Drain global until we see the deletion (other queued messages may
        # arrive first since global is the catch-all scope).
        deadline = time.monotonic() + WAIT_TIMEOUT
        saw_delete = False
        while time.monotonic() < deadline:
            try:
                msg = await asyncio.wait_for(anext(sub_global), timeout=2.0)
            except TimeoutError:
                break
            if msg.kind is ChangeKind.DELETED and msg.classified.kind == "hil":
                saw_delete = True
                print(f"  ✓ saw: hil deletion ({msg.path.name})")
                break
        assert saw_delete, "did not observe HIL deletion within timeout"

        # Final cache state
        assert cache.get_hil(seeds["hil"].id) is None  # type: ignore[attr-defined]
        cur_project = cache.get_project(project.slug)
        assert cur_project is not None and cur_project.name == "renamed"
        cur_stage = cache.get_stage(job.job_slug, stage.stage_id)
        assert cur_stage is not None and cur_stage.state is StageState.SUCCEEDED

        # Tear down watcher
        stop_event.set()
        try:
            await asyncio.wait_for(watcher_task, timeout=2.0)
        except TimeoutError:
            watcher_task.cancel()

        print("smoke OK: bootstrap, watcher, cache, pubsub all working")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
