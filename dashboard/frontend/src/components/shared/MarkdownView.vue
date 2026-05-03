<template>
  <div class="prose prose-invert prose-sm max-w-none" v-html="rendered" />
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";
import remarkRehype from "remark-rehype";
import rehypeHighlight from "rehype-highlight";
import rehypeSanitize from "rehype-sanitize";
import rehypeStringify from "rehype-stringify";

const props = defineProps<{ content: string }>();

const rendered = ref("");

const processor = unified()
  .use(remarkParse)
  .use(remarkGfm)
  .use(remarkRehype)
  .use(rehypeHighlight, { detect: true })
  .use(rehypeSanitize)
  .use(rehypeStringify);

async function render(markdown: string) {
  const result = await processor.process(markdown);
  rendered.value = String(result);
}

watch(() => props.content, render, { immediate: true });
</script>
