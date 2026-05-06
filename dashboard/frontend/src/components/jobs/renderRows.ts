/**
 * Pure helper that turns a flat ``JobDetail.nodes`` list into the
 * left-pane render order: rows interleaved with iteration section
 * headers ("iter 0:", "iter 0, 1:", ...) when entering or transitioning
 * between iteration prefixes.
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
  /** Comma-joined iter prefix, e.g. ``"0"`` or ``"0, 1"``. */
  label: string;
  /** 0 = outermost loop, 1 = inner, ... */
  depth: number;
}

export type RenderRow = NodeRow | HeaderRow;

export function buildRenderedRows(nodes: NodeListEntry[]): RenderRow[] {
  const out: RenderRow[] = [];
  let prevIter: number[] = [];
  for (const entry of nodes) {
    for (let depth = 0; depth < entry.iter.length; depth++) {
      const same = depth < prevIter.length && prevIter[depth] === entry.iter[depth];
      if (!same) {
        out.push({
          kind: "header",
          key: `hdr-${entry.iter.slice(0, depth + 1).join(",")}-${entry.node_id}`,
          label: entry.iter.slice(0, depth + 1).join(", "),
          depth,
        });
      }
    }
    out.push({
      kind: "node",
      key: `${entry.node_id}-${entry.iter.join(",")}`,
      entry,
    });
    prevIter = entry.iter;
  }
  return out;
}
