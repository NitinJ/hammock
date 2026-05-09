<template>
  <div :class="['dag-visualizer w-full', isVertical ? 'overflow-y-auto' : 'overflow-x-auto']">
    <svg
      v-if="layout.width > 0"
      :width="layout.width"
      :height="layout.height"
      :viewBox="`0 0 ${layout.width} ${layout.height}`"
      class="bg-bg-elevated/40 rounded-lg border border-border"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <marker
          id="arrowhead"
          markerWidth="8"
          markerHeight="8"
          refX="6"
          refY="4"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <path d="M0,0 L6,4 L0,8 Z" fill="rgb(82, 88, 105)" />
        </marker>
      </defs>

      <!-- Edges -->
      <g class="edges">
        <path
          v-for="edge in layout.edges"
          :key="`${edge.from}-${edge.to}`"
          :d="edge.d"
          fill="none"
          stroke="rgb(82, 88, 105)"
          stroke-width="1.5"
          marker-end="url(#arrowhead)"
          opacity="0.7"
        />
      </g>

      <!-- Nodes -->
      <g class="nodes">
        <g
          v-for="(box, i) in layout.boxes"
          :key="box.id"
          :transform="`translate(${box.x}, ${box.y})`"
          :data-testid="`dag-node-${box.id}`"
          :class="['transition-opacity', selectable ? 'cursor-pointer' : 'cursor-default']"
          @click="onNodeClick(box.id)"
        >
          <rect
            :width="box.w"
            :height="box.h"
            rx="8"
            ry="8"
            :fill="
              selectedId === box.id
                ? 'rgba(124, 58, 237, 0.25)'
                : box.isExpander
                  ? 'rgba(56, 189, 248, 0.08)'
                  : box.humanReview
                    ? 'rgba(245, 158, 11, 0.08)'
                    : 'rgba(124, 58, 237, 0.08)'
            "
            :stroke="
              selectedId === box.id
                ? 'rgba(124, 58, 237, 0.95)'
                : box.isExpander
                  ? 'rgba(56, 189, 248, 0.6)'
                  : box.humanReview
                    ? 'rgba(245, 158, 11, 0.5)'
                    : 'rgba(124, 58, 237, 0.4)'
            "
            :stroke-width="selectedId === box.id || box.isExpander ? '2' : '1.5'"
            :stroke-dasharray="box.isExpander ? '4 3' : undefined"
          />
          <text
            :x="box.w / 2"
            :y="box.h / 2 + 4"
            text-anchor="middle"
            font-family="ui-monospace, SFMono-Regular, monospace"
            font-size="12"
            fill="rgb(229, 231, 235)"
          >
            {{ truncate(box.id, 20) }}
          </text>
          <text
            v-if="box.humanReview"
            :x="box.w - 6"
            :y="14"
            text-anchor="end"
            font-family="ui-sans-serif, system-ui"
            font-size="9"
            fill="rgb(245, 158, 11)"
            font-weight="600"
          >
            HIL
          </text>
          <text
            v-if="box.isExpander"
            :x="6"
            :y="14"
            text-anchor="start"
            font-family="ui-sans-serif, system-ui"
            font-size="9"
            fill="rgb(56, 189, 248)"
            font-weight="600"
          >
            EXPANDER
          </text>
          <!-- Hidden index for tests + future tooling -->
          <title>{{ box.id }}{{ box.description ? ` — ${box.description}` : "" }}</title>
          <text v-if="i === -1">{{ i }}</text>
        </g>
      </g>
    </svg>
    <p v-else class="text-xs text-text-tertiary p-4">No nodes to render.</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import type { WorkflowNode } from "@/api/types";

const props = withDefaults(
  defineProps<{
    nodes: WorkflowNode[];
    selectable?: boolean;
    selectedId?: string | null;
    direction?: "horizontal" | "vertical";
  }>(),
  { selectable: false, selectedId: null, direction: "horizontal" },
);

const emit = defineEmits<{ (e: "select", id: string): void }>();

const isVertical = computed(() => props.direction === "vertical");

function onNodeClick(id: string) {
  if (props.selectable) emit("select", id);
}

interface BoxLayout {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  humanReview: boolean;
  description: string | null;
  isExpander: boolean;
}

interface EdgeLayout {
  from: string;
  to: string;
  d: string;
}

interface DagLayout {
  width: number;
  height: number;
  boxes: BoxLayout[];
  edges: EdgeLayout[];
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

const layout = computed<DagLayout>(() => {
  const nodes = props.nodes;
  if (nodes.length === 0) {
    return { width: 0, height: 0, boxes: [], edges: [] };
  }

  // Compute level (longest path from a root) per node.
  const byId = new Map<string, WorkflowNode>(nodes.map((n) => [n.id, n]));
  const level = new Map<string, number>();
  function computeLevel(id: string, stack: string[]): number {
    if (level.has(id)) return level.get(id)!;
    if (stack.includes(id)) {
      level.set(id, 0);
      return 0;
    }
    const n = byId.get(id);
    if (!n || n.after.length === 0) {
      level.set(id, 0);
      return 0;
    }
    let maxAfter = -1;
    for (const a of n.after) {
      const sub = computeLevel(a, [...stack, id]);
      if (sub > maxAfter) maxAfter = sub;
    }
    const value = maxAfter + 1;
    level.set(id, value);
    return value;
  }
  for (const n of nodes) computeLevel(n.id, []);

  // Group by level
  const buckets: Record<number, string[]> = {};
  let maxLevel = 0;
  for (const n of nodes) {
    const l = level.get(n.id) ?? 0;
    buckets[l] = buckets[l] ?? [];
    buckets[l].push(n.id);
    if (l > maxLevel) maxLevel = l;
  }

  // Layout constants
  const BOX_W = 180;
  const BOX_H = 40;
  const PAD = 24;
  const vertical = props.direction === "vertical";

  // In horizontal mode: levels = columns (left to right). Within a level, nodes
  // stack vertically.
  // In vertical mode: levels = rows (top to bottom). Within a level, nodes lay
  // out horizontally. This avoids long DAGs overflowing horizontally inside a
  // narrow panel.
  const COL_GAP = vertical ? 24 : 56;
  const ROW_GAP = vertical ? 36 : 18;

  const positions = new Map<string, { x: number; y: number }>();
  let maxColRows = 0;
  for (let l = 0; l <= maxLevel; l++) {
    const ids = buckets[l] ?? [];
    if (ids.length > maxColRows) maxColRows = ids.length;
    for (let i = 0; i < ids.length; i++) {
      const id = ids[i];
      if (id === undefined) continue;
      if (vertical) {
        const x = PAD + i * (BOX_W + COL_GAP);
        const y = PAD + l * (BOX_H + ROW_GAP);
        positions.set(id, { x, y });
      } else {
        const x = PAD + l * (BOX_W + COL_GAP);
        const y = PAD + i * (BOX_H + ROW_GAP);
        positions.set(id, { x, y });
      }
    }
  }

  const width = vertical
    ? PAD + maxColRows * BOX_W + Math.max(0, maxColRows - 1) * COL_GAP + PAD
    : PAD + (maxLevel + 1) * BOX_W + maxLevel * COL_GAP + PAD;
  const height = vertical
    ? PAD + (maxLevel + 1) * BOX_H + maxLevel * ROW_GAP + PAD
    : PAD + maxColRows * BOX_H + Math.max(0, maxColRows - 1) * ROW_GAP + PAD;

  const boxes: BoxLayout[] = nodes.map((n) => {
    const p = positions.get(n.id)!;
    return {
      id: n.id,
      x: p.x,
      y: p.y,
      w: BOX_W,
      h: BOX_H,
      humanReview: n.human_review,
      description: n.description ?? null,
      isExpander: n.kind === "workflow_expander",
    };
  });

  const edges: EdgeLayout[] = [];
  for (const n of nodes) {
    const to = positions.get(n.id);
    if (!to) continue;
    for (const aId of n.after) {
      const from = positions.get(aId);
      if (!from) continue;
      let d: string;
      if (vertical) {
        // Top edge of `to` from bottom edge of `from`.
        const x1 = from.x + BOX_W / 2;
        const y1 = from.y + BOX_H;
        const x2 = to.x + BOX_W / 2;
        const y2 = to.y;
        const cy = (y1 + y2) / 2;
        d = `M${x1},${y1} C${x1},${cy} ${x2},${cy} ${x2},${y2}`;
      } else {
        const x1 = from.x + BOX_W;
        const y1 = from.y + BOX_H / 2;
        const x2 = to.x;
        const y2 = to.y + BOX_H / 2;
        const cx = (x1 + x2) / 2;
        d = `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}`;
      }
      edges.push({ from: aId, to: n.id, d });
    }
  }

  return { width, height, boxes, edges };
});
</script>

<style scoped>
.dag-visualizer svg {
  display: block;
}
</style>
