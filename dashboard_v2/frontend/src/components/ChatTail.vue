<template>
  <section class="space-y-3">
    <div v-if="!chat || chat.turns.length === 0" class="text-text-tertiary text-sm">
      No transcript yet.
    </div>
    <div
      v-else
      v-for="(turn, i) in chat.turns"
      :key="i"
      class="surface p-3 text-sm"
    >
      <header class="flex items-center justify-between mb-2">
        <span class="font-mono text-xs uppercase tracking-wider text-text-tertiary">
          {{ turn.type }}<span v-if="turnSubtype(turn)"> · {{ turnSubtype(turn) }}</span>
        </span>
      </header>
      <template v-if="turn.type === 'assistant'">
        <div v-for="(block, bi) in assistantContent(turn)" :key="bi" class="mb-2">
          <MarkdownView v-if="block.type === 'text'" :source="String(block.text ?? '')" />
          <div
            v-else-if="block.type === 'tool_use'"
            class="font-mono text-xs px-2 py-1.5 rounded-md bg-bg-elevated border border-border inline-block"
          >
            <span class="text-accent-soft">▸ {{ block.name }}</span>
            <span class="text-text-tertiary">({{ summariseInput(block.input) }})</span>
          </div>
        </div>
      </template>
      <template v-else-if="turn.type === 'user'">
        <details v-for="(block, bi) in userContent(turn)" :key="bi" class="mb-2">
          <summary class="cursor-pointer font-mono text-xs text-text-tertiary">
            tool result ({{ approxLen(block.content) }} chars)
          </summary>
          <pre class="mt-2 font-mono text-xs whitespace-pre-wrap text-text-secondary p-2 bg-bg-elevated rounded">{{ String(block.content ?? '') }}</pre>
        </details>
      </template>
      <template v-else-if="turn.type === 'result'">
        <div class="font-mono text-xs text-text-secondary">
          <span :class="resultClass(turn)">{{ turn.is_error ? '✗ failed' : '✓ done' }}</span>
          <span v-if="turn.num_turns" class="ml-2">{{ turn.num_turns }} turns</span>
          <span v-if="turn.total_cost_usd" class="ml-2">${{ formatCost(Number(turn.total_cost_usd)) }}</span>
        </div>
      </template>
      <template v-else-if="turn.type === 'system'">
        <div class="font-mono text-xs text-text-tertiary">
          {{ turn.session_id ? `session ${String(turn.session_id).slice(0,8)}…` : 'system' }}
          <span v-if="turn.cwd" class="ml-2">cwd: {{ turn.cwd }}</span>
        </div>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import MarkdownView from "@/components/MarkdownView.vue";
import type { ChatResponse, ChatTurn } from "@/api/types";

defineProps<{ chat: ChatResponse | undefined }>();

function turnSubtype(turn: ChatTurn): string {
  if (typeof turn.subtype === "string") return turn.subtype;
  return "";
}

function assistantContent(turn: ChatTurn): Array<Record<string, unknown>> {
  const msg = turn.message as { content?: Array<Record<string, unknown>> } | undefined;
  return Array.isArray(msg?.content) ? msg!.content : [];
}

function userContent(turn: ChatTurn): Array<Record<string, unknown>> {
  const msg = turn.message as { content?: Array<Record<string, unknown>> } | undefined;
  return (msg?.content ?? []).filter((b) => (b as { type?: string }).type === "tool_result");
}

function summariseInput(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const obj = input as Record<string, unknown>;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (parts.length >= 2) break;
    const s = typeof v === "string" ? v : JSON.stringify(v);
    if (s && s.length > 60) parts.push(`${k}=${s.slice(0, 57)}…`);
    else parts.push(`${k}=${s}`);
  }
  return parts.join(", ");
}

function approxLen(content: unknown): number {
  if (typeof content === "string") return content.length;
  if (Array.isArray(content)) return JSON.stringify(content).length;
  return 0;
}

function resultClass(turn: ChatTurn): string {
  return turn.is_error ? "text-state-failed" : "text-state-succeeded";
}

function formatCost(n: number): string {
  return n.toFixed(3);
}
</script>
