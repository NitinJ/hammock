<template>
  <StubView
    :title="`Artifact: ${artifactPath}`"
    icon="📄"
    description="Markdown / YAML / JSON viewer with raw toggle. Lands in Stage 12."
  />
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import StubView from "@/components/shared/StubView.vue";

const route = useRoute();
const artifactPath = computed(() => {
  const p = route.params["path"];
  return Array.isArray(p) ? p.join("/") : (p ?? "");
});
</script>
