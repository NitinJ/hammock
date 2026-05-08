<template>
  <!-- eslint-disable-next-line vue/no-v-html -- html is sanitized via rehype-sanitize in renderMarkdown -->
  <div class="markdown-body" v-html="html" />
</template>

<script setup lang="ts">
import { computed } from "vue";
import { renderMarkdown } from "@/lib/markdown";

const props = defineProps<{ source: string }>();
const html = computed(() => renderMarkdown(props.source ?? ""));
</script>
