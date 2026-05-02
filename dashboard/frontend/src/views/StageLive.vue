<template>
  <StubView
    :title="`Stage: ${stageId} (${jobSlug})`"
    icon="▶"
    description="Three-pane live view: tasks + HIL items / Agent0 stream pane / inputs + outputs + budget. Lands in Stage 15."
  />
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import StubView from "@/components/shared/StubView.vue";

const route = useRoute();
const jobSlug = computed(() => route.params["jobSlug"] as string);
const stageId = computed(() => route.params["stageId"] as string);
</script>
