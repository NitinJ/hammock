<template>
  <section class="surface bg-state-awaiting/5 border-state-awaiting/40 p-4 space-y-3">
    <header class="flex items-center gap-2">
      <span class="size-2 rounded-full bg-state-awaiting animate-pulse" />
      <h3 class="text-sm font-medium text-state-awaiting">Awaiting your review</h3>
    </header>
    <p class="text-xs text-text-secondary">
      The agent has produced a review. Approve to continue, or request a revision with feedback.
    </p>
    <textarea
      v-model="comment"
      rows="3"
      placeholder="Optional comment (required for needs-revision)"
      class="input font-mono text-xs"
    />
    <div v-if="error" class="text-state-failed text-xs">{{ error }}</div>
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="btn-accent text-xs"
        :disabled="submit.isPending.value"
        @click="onApprove"
      >
        Approve
      </button>
      <button
        type="button"
        class="btn-ghost text-xs"
        :disabled="submit.isPending.value || !comment.trim()"
        @click="onReject"
      >
        Needs revision
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

import { useSubmitDecision } from "@/api/queries";

const props = defineProps<{ slug: string; nodeId: string }>();
const slugRef = computed(() => props.slug);
const nodeIdRef = computed(() => props.nodeId);

const submit = useSubmitDecision(slugRef, nodeIdRef);
const comment = ref("");
const error = ref<string | null>(null);

async function onApprove(): Promise<void> {
  error.value = null;
  try {
    await submit.mutateAsync({
      decision: "approved",
      comment: comment.value.trim() || undefined,
    });
    comment.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function onReject(): Promise<void> {
  error.value = null;
  if (!comment.value.trim()) {
    error.value = "Please describe what needs to change.";
    return;
  }
  try {
    await submit.mutateAsync({
      decision: "needs-revision",
      comment: comment.value.trim(),
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>
