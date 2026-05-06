/**
 * SSE → vue-query invalidation router.
 *
 * The watcher emits a ``LiveSseEvent`` for every file change under the
 * Hammock root, classified by ``file_kind``. We map each kind to the
 * query keys whose responses depend on that path, then call
 * ``invalidateQueries`` so vue-query refetches them.
 *
 * Replay events (with ``seq``) are *not* routed here — they're already
 * a stream of past data. Only live changes drive cache invalidation.
 */

import type { QueryClient } from "@tanstack/vue-query";
import type { LiveSseEvent, SseEvent } from "./schema.d";

export function applySseInvalidation(qc: QueryClient, event: SseEvent): void {
  if ("seq" in event) return; // replay event, not a path change
  const live = event as LiveSseEvent;

  switch (live.file_kind) {
    case "job": {
      // Job state file mutated → list ordering / state badges may shift,
      // and the detail row's state changes too.
      qc.invalidateQueries({ queryKey: ["jobs"] });
      return;
    }
    case "node": {
      // node/<slug>/<id>/state.json — affects job detail (left pane state
      // badge) and per-node detail (state, attempts, last_error).
      if (live.job_slug) {
        qc.invalidateQueries({ queryKey: ["jobs", "detail", live.job_slug] });
        if (live.node_id) {
          qc.invalidateQueries({
            queryKey: ["jobs", live.job_slug, "nodes", live.node_id],
          });
        }
      }
      return;
    }
    case "variable":
    case "loop_variable": {
      // A new envelope landed → loop iteration count may grow (so the
      // job detail's row list grows) and node detail outputs change.
      // We don't know the producing node id from a variable path alone,
      // so we invalidate the whole job's node-detail cache.
      if (live.job_slug) {
        qc.invalidateQueries({ queryKey: ["jobs", "detail", live.job_slug] });
        qc.invalidateQueries({ queryKey: ["jobs", live.job_slug, "nodes"] });
      }
      return;
    }
    case "pending":
    case "ask": {
      // HIL marker added/removed → inbox count and per-job HIL view change;
      // job state may also flip to/from BLOCKED_ON_HUMAN.
      qc.invalidateQueries({ queryKey: ["hil"] });
      if (live.job_slug) {
        qc.invalidateQueries({ queryKey: ["jobs", "detail", live.job_slug] });
      }
      return;
    }
    case "project": {
      qc.invalidateQueries({ queryKey: ["projects"] });
      return;
    }
    case "events_jsonl":
    case "unknown":
    default:
      // events.jsonl is consumed via SSE replay, not vue-query.
      return;
  }
}
