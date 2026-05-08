/**
 * SSE → vue-query invalidation routing — Stage D additions only.
 *
 * The Stage C backend emits PathChange wire events with
 * `file_kind: "chat_jsonl"` whenever a node's chat.jsonl is appended
 * to. Stage D dispatches on `file_kind` (no separate `chat_appended`
 * SSE event name) and invalidates the matching `useAgentChat` cache.
 */

import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/vue-query";
import { applySseInvalidation } from "@/api/invalidation";
import type { LiveSseEvent } from "@/api/schema.d";

function makeClient(): {
  qc: QueryClient;
  invalidate: ReturnType<typeof vi.fn>;
} {
  const qc = new QueryClient();
  const invalidate = vi.fn();
  qc.invalidateQueries = invalidate as unknown as QueryClient["invalidateQueries"];
  return { qc, invalidate };
}

describe("applySseInvalidation — chat_jsonl", () => {
  it("invalidates the matching agentChat key", () => {
    const { qc, invalidate } = makeClient();
    const event: LiveSseEvent = {
      scope: "global",
      change_kind: "modified",
      file_kind: "chat_jsonl",
      job_slug: "j1",
      node_id: "write-design-spec",
      iter: [0, 1],
      attempt: 2,
    };

    applySseInvalidation(qc, event);

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["jobs", "j1", "nodes", "write-design-spec", "iter", "i0_1", "chat", 2],
    });
  });

  it("uses 'top' iter_token for top-level executions", () => {
    const { qc, invalidate } = makeClient();
    const event: LiveSseEvent = {
      scope: "global",
      change_kind: "modified",
      file_kind: "chat_jsonl",
      job_slug: "j1",
      node_id: "n1",
      iter: [],
      attempt: 1,
    };

    applySseInvalidation(qc, event);

    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["jobs", "j1", "nodes", "n1", "iter", "top", "chat", 1],
    });
  });

  it("ignores chat_jsonl events missing the iter or attempt axis", () => {
    const { qc, invalidate } = makeClient();
    applySseInvalidation(qc, {
      scope: "global",
      change_kind: "modified",
      file_kind: "chat_jsonl",
      job_slug: "j1",
      node_id: "n1",
      // no iter, no attempt
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("applySseInvalidation — node with iter", () => {
  it("narrows invalidation to the iter-scoped node key when present", () => {
    const { qc, invalidate } = makeClient();
    applySseInvalidation(qc, {
      scope: "global",
      change_kind: "modified",
      file_kind: "node",
      job_slug: "j1",
      node_id: "n1",
      iter: [0],
    });
    // Job detail + node-iter detail both invalidated.
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["jobs", "detail", "j1"],
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["jobs", "j1", "nodes", "n1", "iter", "i0"],
    });
  });

  it("falls back to all-iter invalidation when iter is missing", () => {
    const { qc, invalidate } = makeClient();
    applySseInvalidation(qc, {
      scope: "global",
      change_kind: "modified",
      file_kind: "node",
      job_slug: "j1",
      node_id: "n1",
    });
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["jobs", "j1", "nodes", "n1"],
    });
  });
});
