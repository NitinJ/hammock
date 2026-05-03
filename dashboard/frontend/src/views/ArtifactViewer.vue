<template>
  <div class="p-6 space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="font-mono text-lg text-text-primary">{{ artifactPath }}</h1>
      <button
        v-if="content"
        class="text-sm text-text-secondary hover:text-text-primary border border-border rounded px-3 py-1"
        @click="showRaw = !showRaw"
      >
        {{ showRaw ? "Rendered" : "Raw" }}
      </button>
    </div>

    <div v-if="isPending" class="text-text-secondary text-sm">Loading artifact…</div>
    <div v-else-if="isError" class="text-red-400 text-sm">Failed to load artifact.</div>
    <template v-else-if="content !== undefined">
      <pre v-if="showRaw || isRaw" class="bg-surface border border-border rounded p-4 text-sm font-mono overflow-x-auto whitespace-pre-wrap">{{ content }}</pre>
      <MarkdownView v-else :content="content" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRoute } from "vue-router";
import { useArtifact } from "@/api/queries";
import MarkdownView from "@/components/shared/MarkdownView.vue";

const route = useRoute();

const artifactPath = computed(() => {
  const p = route.params["path"];
  return Array.isArray(p) ? p.join("/") : ((p as string) ?? "");
});

const jobSlug = computed(() => route.params["jobSlug"] as string);

const { data: content, isPending, isError } = useArtifact(jobSlug, artifactPath);

const showRaw = ref(false);

const isRaw = computed(() => {
  const path = artifactPath.value;
  return path.endsWith(".yaml") || path.endsWith(".yml") || path.endsWith(".json");
});
</script>
