<template>
  <div class="space-y-4">
    <div>
      <div class="text-xs uppercase text-text-secondary">Question</div>
      <p class="mt-1 whitespace-pre-wrap text-text-primary">
        {{ question }}
      </p>
    </div>

    <form class="space-y-2" @submit.prevent="handleSubmit">
      <label class="block text-xs uppercase text-text-secondary">Answer</label>
      <textarea
        v-model="answer"
        class="block w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
        rows="6"
        placeholder="Type your answer…"
      />
      <div class="flex items-center gap-3">
        <button
          type="submit"
          :disabled="!canSubmit || submitting"
          class="rounded-md border border-blue-500 bg-blue-500/20 px-3 py-1.5 text-sm text-blue-200 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {{ submitting ? "Submitting…" : "Submit" }}
        </button>
        <span v-if="error" class="text-sm text-red-400">{{ error }}</span>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

const props = defineProps<{
  question: string;
  onSubmit: (answer: string) => Promise<void>;
}>();

const answer = ref("");
const submitting = ref(false);
const error = ref<string | null>(null);

const canSubmit = computed(() => answer.value.trim().length > 0);

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return;
  submitting.value = true;
  error.value = null;
  try {
    await props.onSubmit(answer.value);
    answer.value = "";
  } catch (e) {
    error.value = (e as Error).message ?? String(e);
  } finally {
    submitting.value = false;
  }
}
</script>
