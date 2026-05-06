/**
 * Pure helper that turns a flat ``JobDetail.nodes`` list into the
 * left-pane render order: rows interleaved with section headers when
 * entering or transitioning between loop iterations.
 *
 * Header diffing keys on the ``(loop_path[depth], iter[depth])`` pair —
 * not just ``iter`` — so sibling top-level loops whose body iterations
 * happen to coincide at the same indices (e.g. all at iter 0) get
 * distinct headers labelled with the loop_id.
 *
 * Extracted so it can be unit-tested without mounting JobOverview.
 */

import type { NodeListEntry } from "@/api/schema.d";

export interface NodeRow {
  kind: "node";
  key: string;
  entry: NodeListEntry;
}

export interface HeaderRow {
  kind: "header";
  key: string;
  /** Display label, e.g. ``"design-spec-loop · iter 0"`` or
   *  ``"design-spec-agent-loop · iter 0"`` for a nested header. */
  label: string;
  /** 0 = outermost loop, 1 = inner, ... */
  depth: number;
}

export type RenderRow = NodeRow | HeaderRow;

export function buildRenderedRows(nodes: NodeListEntry[]): RenderRow[] {
  const out: RenderRow[] = [];
  let prev: NodeListEntry | null = null;
  for (const entry of nodes) {
    const depthCount = entry.iter.length;
    for (let depth = 0; depth < depthCount; depth++) {
      const sameDepthValid =
        prev !== null && depth < prev.iter.length && depth < prev.loop_path.length;
      const same =
        sameDepthValid &&
        prev!.iter[depth] === entry.iter[depth] &&
        prev!.loop_path[depth] === entry.loop_path[depth];
      if (!same) {
        const loopId = entry.loop_path[depth];
        const iterIdx = entry.iter[depth];
        out.push({
          kind: "header",
          key: headerKey(entry, depth),
          label: `${loopId} · iter ${iterIdx}`,
          depth,
        });
      }
    }
    out.push({
      kind: "node",
      key: `${entry.node_id}-${entry.loop_path.join("/")}-${entry.iter.join(",")}`,
      entry,
    });
    prev = entry;
  }
  return out;
}

function headerKey(entry: NodeListEntry, depth: number): string {
  const loops = entry.loop_path.slice(0, depth + 1).join("/");
  const iters = entry.iter.slice(0, depth + 1).join(",");
  return `hdr-${loops}-${iters}`;
}
