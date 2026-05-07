<template>
  <div class="space-y-3">
    <!-- eslint-disable vue/no-v-html -->
    <article
      v-if="documentMarkdown"
      class="prose prose-invert prose-sm max-w-none rounded-md border border-border bg-surface px-4 py-3"
      v-html="renderedHtml"
    />
    <!-- eslint-enable vue/no-v-html -->

    <!-- Metadata panel: typed fields other than `document` -->
    <details
      v-if="documentMarkdown && hasMetadata"
      class="rounded-md border border-border bg-surface px-3 py-2 text-xs text-text-secondary"
    >
      <summary class="cursor-pointer text-text-primary">Metadata</summary>
      <pre class="mt-2 overflow-auto">{{ JSON.stringify(metadataValue, null, 2) }}</pre>
    </details>

    <!-- Fallback: no `document`, render the envelope as-is. -->
    <pre
      v-if="!documentMarkdown"
      class="max-h-96 overflow-auto rounded-md border border-border bg-surface px-3 py-2 text-xs text-text-primary"
      >{{ name }}: {{ JSON.stringify(envelope, null, 2) }}</pre
    >
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { EnvelopePayload } from "@/api/schema.d";
import { renderMarkdown } from "@/lib/markdown";

const props = defineProps<{
  /** Display name for the envelope (typically the variable name). */
  name: string;
  envelope: EnvelopePayload;
}>();

/** The markdown source from the envelope's value, if any. */
const documentMarkdown = computed<string | null>(() => {
  const v = props.envelope?.value;
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const doc = (v as Record<string, unknown>).document;
    if (typeof doc === "string" && doc.trim().length > 0) return doc;
  }
  return null;
});

/** Other typed fields, surfaced as a collapsible metadata panel. */
const metadataValue = computed<Record<string, unknown> | null>(() => {
  const v = props.envelope?.value;
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  const out: Record<string, unknown> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    if (k === "document") continue;
    out[k] = val;
  }
  return Object.keys(out).length > 0 ? out : null;
});

const hasMetadata = computed(() => metadataValue.value !== null);

/** Async-rendered HTML from the markdown source. */
const renderedHtml = ref<string>("");

watch(
  documentMarkdown,
  async (md) => {
    if (md == null) {
      renderedHtml.value = "";
      return;
    }
    renderedHtml.value = await renderMarkdown(md);
  },
  { immediate: true },
);
</script>
