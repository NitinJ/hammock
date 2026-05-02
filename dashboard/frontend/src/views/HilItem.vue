<template>
  <StubView
    :title="`HIL Item: ${itemId}`"
    icon="📝"
    description="Form rendered from ui_template — ask / review / manual-step. Lands in Stage 13."
  />
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import StubView from "@/components/shared/StubView.vue";

const route = useRoute();
const itemId = computed(() => route.params["itemId"] as string);
</script>
