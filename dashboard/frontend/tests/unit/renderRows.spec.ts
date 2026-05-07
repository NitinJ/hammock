import { describe, expect, it } from "vitest";
import { buildRenderedRows } from "@/components/jobs/renderRows";
import type { NodeListEntry } from "@/api/schema.d";

function n(
  node_id: string,
  iter: number[] = [],
  loop_path: string[] = [],
  parent_loop_id: string | null = null,
): NodeListEntry {
  return {
    node_id,
    name: null,
    kind: "artifact",
    actor: "agent",
    state: "succeeded",
    attempts: 1,
    last_error: null,
    started_at: null,
    finished_at: null,
    iter,
    loop_path,
    parent_loop_id,
  };
}

describe("buildRenderedRows", () => {
  it("emits one node row per top-level entry, no headers", () => {
    const rows = buildRenderedRows([n("a"), n("b")]);
    expect(rows.map((r) => r.kind)).toEqual(["node", "node"]);
  });

  it("inserts a header before the first row of a new iteration", () => {
    const rows = buildRenderedRows([
      n("body", [0], ["loop1"], "loop1"),
      n("body", [1], ["loop1"], "loop1"),
    ]);
    const kinds = rows.map((r) => r.kind);
    const labels = rows.map((r) => (r.kind === "header" ? r.label : null));
    expect(kinds).toEqual(["header", "node", "header", "node"]);
    expect(labels).toEqual(["loop1 · iter 0", null, "loop1 · iter 1", null]);
  });

  it("nests headers per nesting level for nested loops", () => {
    const rows = buildRenderedRows([
      n("leaf", [0, 0], ["outer", "inner"], "inner"),
      n("leaf", [0, 1], ["outer", "inner"], "inner"),
      n("leaf", [1, 0], ["outer", "inner"], "inner"),
    ]);
    const labels = rows
      .filter((r) => r.kind === "header")
      .map((r) => (r as { label: string }).label);
    // First entry iter=[0,0]: emit outer "outer · iter 0" then inner "inner · iter 0".
    // iter=[0,1]: outer same, inner changed → "inner · iter 1".
    // iter=[1,0]: both changed → "outer · iter 1" then "inner · iter 0".
    expect(labels).toEqual([
      "outer · iter 0",
      "inner · iter 0",
      "inner · iter 1",
      "outer · iter 1",
      "inner · iter 0",
    ]);
  });

  it("does NOT re-emit a header when the same iter prefix continues", () => {
    const rows = buildRenderedRows([
      n("a", [0], ["loop1"], "loop1"),
      n("b", [0], ["loop1"], "loop1"),
    ]);
    const headers = rows.filter((r) => r.kind === "header");
    expect(headers).toHaveLength(1);
  });

  it("handles a top-level node followed by a loop body cleanly", () => {
    const rows = buildRenderedRows([n("setup"), n("body", [0], ["loop1"], "loop1")]);
    expect(rows.map((r) => r.kind)).toEqual(["node", "header", "node"]);
  });

  it("breaks the header per loop when sibling loops have body rows at the same iter index", () => {
    // Two sibling top-level loops, each with one body row at iter [0].
    // Without the loop_path diff, both bodies would bucket under one
    // shared "iter 0" header. With the fix, each gets its own header
    // labelled by the loop_id.
    const rows = buildRenderedRows([
      n("body-a", [0], ["loop-a"], "loop-a"),
      n("body-b", [0], ["loop-b"], "loop-b"),
    ]);
    const headers = rows.filter((r) => r.kind === "header") as { label: string }[];
    expect(headers).toHaveLength(2);
    expect(headers.map((h) => h.label)).toEqual(["loop-a · iter 0", "loop-b · iter 0"]);
  });

  it("uses loop_names map for section labels when provided", () => {
    const rows = buildRenderedRows(
      [n("body", [0], ["impl-loop"], "impl-loop")],
      { "impl-loop": "Implement step" },
    );
    const headers = rows.filter((r) => r.kind === "header") as { label: string }[];
    expect(headers).toHaveLength(1);
    expect(headers[0]?.label).toBe("Implement step · iter 0");
  });

  it("falls back to loop_id when loop_names lacks an entry", () => {
    const rows = buildRenderedRows([n("body", [0], ["unnamed"], "unnamed")], {});
    const headers = rows.filter((r) => r.kind === "header") as { label: string }[];
    expect(headers[0]?.label).toBe("unnamed · iter 0");
  });
});
