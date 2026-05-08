<template>
  <div class="flex h-full min-h-0 flex-col" data-testid="agent-chat-tail">
    <div class="border-b border-border px-3 py-2 text-xs uppercase text-text-secondary">Chat</div>

    <div v-if="chat.isPending.value" class="px-3 py-3 text-sm text-text-secondary">
      Loading transcript…
    </div>
    <div v-else-if="chat.isError.value" class="px-3 py-3 text-sm text-red-400">
      Failed to load transcript: {{ chat.error.value?.message ?? "unknown error" }}
    </div>
    <div
      v-else-if="!chat.data.value?.has_chat"
      data-testid="agent-chat-empty"
      class="px-3 py-3 text-sm text-text-secondary"
    >
      No chat transcript for this run.
    </div>
    <div v-else ref="scrollerRef" class="min-h-0 flex-1 space-y-3 overflow-auto px-3 py-3">
      <template v-for="(turn, idx) in chat.data.value.turns" :key="idx">
        <!-- system: tiny grey one-liner -->
        <div
          v-if="turn.type === 'system'"
          class="font-mono text-[11px] text-text-secondary"
          data-testid="chat-turn-system"
        >
          system · session={{ shortStr(turn.session_id) }}
          <span v-if="turn.cwd"> · cwd={{ turn.cwd }}</span>
        </div>

        <!-- assistant: chat bubble of content blocks -->
        <div
          v-else-if="turn.type === 'assistant'"
          class="rounded-md border border-border bg-surface px-3 py-2"
          data-testid="chat-turn-assistant"
        >
          <div class="mb-1 text-[11px] uppercase text-text-secondary">assistant</div>
          <template v-for="(block, bidx) in assistantContent(turn)" :key="bidx">
            <!-- text → markdown -->
            <!-- eslint-disable vue/no-v-html -->
            <article
              v-if="block.type === 'text'"
              class="prose prose-invert prose-sm max-w-none"
              data-testid="chat-block-text"
              v-html="renderedText[`${idx}.${bidx}`] ?? ''"
            />
            <!-- eslint-enable vue/no-v-html -->
            <!-- tool_use → chip -->
            <div
              v-else-if="block.type === 'tool_use'"
              class="rounded border border-border bg-surface-raised px-2 py-1 font-mono text-xs text-text-primary"
              data-testid="chat-block-tool-use"
            >
              ▸ {{ block.name ?? "tool" }}({{ truncate(toolUseSummary(block.input), 80) }})
            </div>
          </template>
        </div>

        <!-- user: tool_results, collapsed by default -->
        <div v-else-if="turn.type === 'user'" class="space-y-1" data-testid="chat-turn-user">
          <template v-for="(block, bidx) in userContent(turn)" :key="bidx">
            <details
              v-if="block.type === 'tool_result'"
              class="rounded-md border border-border bg-surface px-3 py-1 text-xs text-text-secondary"
              data-testid="chat-block-tool-result"
            >
              <summary class="cursor-pointer text-text-primary">
                tool result ({{ toolResultLength(block) }} chars)
              </summary>
              <pre class="mt-2 overflow-auto whitespace-pre-wrap text-text-primary">{{
                toolResultContent(block)
              }}</pre>
            </details>
          </template>
        </div>

        <!-- result footer -->
        <div
          v-else-if="turn.type === 'result'"
          class="border-t border-border pt-2 text-xs text-text-secondary"
          data-testid="chat-turn-result"
        >
          <span v-if="turn.is_error" class="text-red-400">✗ Error</span>
          <span v-else>✓ Done</span>
          <span v-if="turn.num_turns != null"> · {{ turn.num_turns }} turns</span>
          <span v-if="turn.total_cost_usd != null"> · ${{ formatCost(turn.total_cost_usd) }} </span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useAgentChat } from "@/api/queries";
import { renderMarkdown } from "@/lib/markdown";

const props = withDefaults(
  defineProps<{
    jobSlug: string;
    nodeId: string;
    /** Iteration coordinates; empty array = top-level execution. */
    iterPath?: readonly number[];
    attempt?: number;
  }>(),
  { attempt: 1, iterPath: () => [] },
);

const jobSlugRef = computed(() => props.jobSlug);
const nodeIdRef = computed(() => props.nodeId);
const iterPathRef = computed<readonly number[]>(() => props.iterPath ?? []);
const attemptRef = computed(() => props.attempt);

const chat = useAgentChat(jobSlugRef, nodeIdRef, iterPathRef, attemptRef);

const scrollerRef = ref<HTMLElement | null>(null);

/** Pre-rendered markdown HTML, keyed by `${turnIdx}.${blockIdx}`. We
 *  pre-compute on data change so v-html stays synchronous. */
const renderedText = ref<Record<string, string>>({});

/** Pixel slack for "user is at the bottom". Below this threshold from
 *  the bottom we auto-scroll on new turns; above it we leave the
 *  scroll position alone so the user can read older turns without
 *  the live update yanking them down. */
const AUTO_SCROLL_THRESHOLD_PX = 50;

let priorTurnCount = 0;

watch(
  () => chat.data.value?.turns,
  async (turns) => {
    if (!turns) {
      renderedText.value = {};
      priorTurnCount = 0;
      return;
    }

    // Capture scroll intent BEFORE we do any async markdown work so
    // we read the user's actual viewport, not a state we mutated.
    const isInitialLoad = priorTurnCount === 0;
    const wasAtBottom = isScrollerAtBottom();

    const next: Record<string, string> = {};
    for (let i = 0; i < turns.length; i++) {
      const t = turns[i];
      if (!t || typeof t !== "object") continue;
      const tt = t as Record<string, unknown>;
      if (tt.type !== "assistant") continue;
      const blocks = assistantContent(tt);
      for (let b = 0; b < blocks.length; b++) {
        const blk = blocks[b];
        if (blk && blk.type === "text" && typeof blk.text === "string") {
          next[`${i}.${b}`] = await renderMarkdown(blk.text);
        }
      }
    }
    renderedText.value = next;
    priorTurnCount = turns.length;

    await nextTick();
    // Only auto-scroll on initial render or when the user was already
    // at the bottom; otherwise preserve scroll so SSE pokes don't yank
    // the viewport while the user is reading earlier turns.
    if ((isInitialLoad || wasAtBottom) && scrollerRef.value) {
      scrollerRef.value.scrollTop = scrollerRef.value.scrollHeight;
    }
  },
  { immediate: true },
);

function isScrollerAtBottom(): boolean {
  const el = scrollerRef.value;
  if (!el) return true;
  const distanceFromBottom = el.scrollHeight - el.clientHeight - el.scrollTop;
  return distanceFromBottom <= AUTO_SCROLL_THRESHOLD_PX;
}

interface ContentBlock {
  type: string;
  text?: string;
  name?: string;
  input?: unknown;
  content?: unknown;
}

function assistantContent(turn: Record<string, unknown>): ContentBlock[] {
  const msg = turn.message;
  if (!msg || typeof msg !== "object") return [];
  const content = (msg as Record<string, unknown>).content;
  if (!Array.isArray(content)) return [];
  return content as ContentBlock[];
}

function userContent(turn: Record<string, unknown>): ContentBlock[] {
  const msg = turn.message;
  if (!msg || typeof msg !== "object") return [];
  const content = (msg as Record<string, unknown>).content;
  if (!Array.isArray(content)) return [];
  return content as ContentBlock[];
}

function toolUseSummary(input: unknown): string {
  if (input == null) return "";
  if (typeof input !== "object") return String(input);
  // Render the first 1-2 string fields, e.g. file_path, pattern, command.
  const obj = input as Record<string, unknown>;
  const pieces: string[] = [];
  let count = 0;
  for (const [k, v] of Object.entries(obj)) {
    if (count >= 2) break;
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
      pieces.push(`${k}=${JSON.stringify(v)}`);
      count++;
    }
  }
  return pieces.join(", ");
}

function toolResultContent(block: ContentBlock): string {
  const c = block.content;
  if (typeof c === "string") return c;
  if (Array.isArray(c)) {
    return c
      .map((part: unknown) => {
        if (typeof part === "string") return part;
        if (part && typeof part === "object") {
          const p = part as Record<string, unknown>;
          if (typeof p.text === "string") return p.text;
        }
        return JSON.stringify(part);
      })
      .join("\n");
  }
  return JSON.stringify(c ?? "");
}

function toolResultLength(block: ContentBlock): number {
  return toolResultContent(block).length;
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function shortStr(value: unknown): string {
  if (typeof value !== "string") return "?";
  if (value.length <= 8) return value;
  return value.slice(0, 8);
}

function formatCost(value: unknown): string {
  if (typeof value !== "number") return String(value);
  return value.toFixed(4);
}
</script>
