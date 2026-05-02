<template>
  <!-- Stage 12 will wire unified/remark here. Stub renders raw text for now. -->
  <div class="prose prose-invert prose-sm max-w-none" v-html="rendered" />
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ content: string }>();

// TODO (Stage 12): replace with unified + remark-gfm + rehype-highlight pipeline
const rendered = computed(() => {
  // Minimal pre-escaping — just enough to not inject raw HTML from content
  const escaped = props.content
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return `<pre class="whitespace-pre-wrap font-mono text-xs">${escaped}</pre>`;
});
</script>
