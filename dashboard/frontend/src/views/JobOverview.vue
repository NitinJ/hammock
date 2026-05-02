<template>
  <StubView
    :title="`Job: ${jobSlug}`"
    icon="📋"
    description="Stage timeline, artifacts panel, PR strip, cost panel. Lands in Stage 12."
  />
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import StubView from "@/components/shared/StubView.vue";

const route = useRoute();
const jobSlug = computed(() => route.params["jobSlug"] as string);
</script>
