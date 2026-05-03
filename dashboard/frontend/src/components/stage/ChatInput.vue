<template>
  <form class="flex gap-2 border-t border-border pt-2" @submit.prevent="handleSubmit">
    <textarea
      v-model="text"
      class="flex-1 rounded border border-border bg-surface px-2 py-1 text-sm resize-none"
      rows="2"
      placeholder="Send a message to the agent..."
      @keydown.enter.exact.prevent="handleSubmit"
    />
    <button
      type="submit"
      class="px-3 py-1 rounded bg-primary text-white text-sm font-medium disabled:opacity-50"
      :disabled="!text.trim()"
    >
      Send
    </button>
  </form>
</template>

<script setup lang="ts">
import { ref } from "vue";

defineProps<{ jobSlug: string; stageId: string }>();
const emit = defineEmits<{ (e: "send", text: string): void }>();

const text = ref("");

function handleSubmit(): void {
  const trimmed = text.value.trim();
  if (!trimmed) return;
  emit("send", trimmed);
  text.value = "";
}
</script>
