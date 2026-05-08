<template>
  <section class="space-y-4">
    <header class="surface p-5 flex items-center justify-between gap-3">
      <div>
        <RouterLink
          :to="{ name: 'job-detail', params: { slug } }"
          class="text-xs text-text-tertiary hover:text-text-secondary"
        >
          ← {{ slug }}
        </RouterLink>
        <h1 class="font-mono text-base font-medium text-text-primary mt-1">
          orchestrator transcript
        </h1>
      </div>
    </header>
    <ChatTail :chat="chat.data.value" />
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";

import ChatTail from "@/components/ChatTail.vue";
import { useOrchestratorChat } from "@/api/queries";

const props = defineProps<{ slug: string }>();
const slugRef = computed(() => props.slug);
const chat = useOrchestratorChat(slugRef);
</script>
