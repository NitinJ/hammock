<template>
  <StubView
    :title="`Project: ${slug}`"
    icon="⊡"
    description="Project metadata, job list, last doctor report, override inventory. Lands in Stage 12."
  />
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import StubView from "@/components/shared/StubView.vue";

const route = useRoute();
const slug = computed(() => route.params["slug"] as string);
</script>
