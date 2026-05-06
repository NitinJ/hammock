import { describe, expect, it } from "vitest";
import { buildRenderedRows } from "@/components/jobs/renderRows";
import type { NodeListEntry } from "@/api/schema.d";

function n(node_id: string, iter: number[] = [], parent_loop_id: string | null = null): NodeListEntry {
  return {
    node_id,
    kind: "artifact",
    actor: "agent",
    state: "succeeded",
    attempts: 1,
    last_error: null,
    started_at: null,
    finished_at: null,
    iter,
    parent_loop_id,
  };
}

describe("buildRenderedRows", () => {
  it("emits one node row per top-level entry, no headers", () => {
    const rows = buildRenderedRows([n("a"), n("b")]);
    expect(rows.map((r) => r.kind)).toEqual(["node", "node"]);
  });

  it("inserts an iter header before the first row of a new iteration", () => {
    const rows = buildRenderedRows([
      n("body", [0], "loop1"),
      n("body", [1], "loop1"),
    ]);
    const kinds = rows.map((r) => r.kind);
    const labels = rows.map((r) => (r.kind === "header" ? r.label : null));
    expect(kinds).toEqual(["header", "node", "header", "node"]);
    expect(labels).toEqual(["0", null, "1", null]);
  });

  it("nests headers per nesting level for nested loops", () => {
    const rows = buildRenderedRows([
      n("leaf", [0, 0], "inner"),
      n("leaf", [0, 1], "inner"),
      n("leaf", [1, 0], "inner"),
    ]);
    const labels = rows.filter((r) => r.kind === "header").map((r) => (r as { label: string }).label);
    // First entry iter=[0,0]: emit outer "0" then inner "0, 0".
    // iter=[0,1]: outer same, inner changed → "0, 1".
    // iter=[1,0]: both changed → "1" then "1, 0".
    expect(labels).toEqual(["0", "0, 0", "0, 1", "1", "1, 0"]);
  });

  it("does NOT re-emit a header when the same iter prefix continues", () => {
    const rows = buildRenderedRows([
      n("a", [0], "loop1"),
      n("b", [0], "loop1"),
    ]);
    const headers = rows.filter((r) => r.kind === "header");
    expect(headers).toHaveLength(1);
  });

  it("handles a top-level node followed by a loop body cleanly", () => {
    const rows = buildRenderedRows([
      n("setup"),
      n("body", [0], "loop1"),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(["node", "header", "node"]);
  });
});
