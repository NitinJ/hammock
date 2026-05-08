<template>
  <section class="surface bg-state-awaiting/5 border-state-awaiting/40 p-5 space-y-4">
    <header class="flex items-center gap-2">
      <span class="size-2 rounded-full bg-state-awaiting animate-pulse" />
      <h3 class="text-sm font-medium text-state-awaiting">Awaiting your review</h3>
    </header>

    <section v-if="reviewMd" class="space-y-2">
      <div class="text-xs uppercase tracking-wider text-text-tertiary">Agent's review</div>
      <div
        class="rounded-lg bg-bg-elevated/40 border border-border p-4 max-h-[40vh] overflow-y-auto"
      >
        <MarkdownView :source="reviewMd" />
      </div>
    </section>
    <p v-else class="text-text-tertiary text-xs italic">
      No review content yet. Check back in a moment or open the Output tab.
    </p>

    <section class="space-y-3">
      <div class="text-xs uppercase tracking-wider text-text-tertiary">Your decision</div>
      <div class="flex items-center gap-3 text-sm">
        <label class="inline-flex items-center gap-2 cursor-pointer">
          <input
            v-model="decisionChoice"
            type="radio"
            value="approved"
            class="accent-state-succeeded"
          />
          <span>Approve</span>
        </label>
        <label class="inline-flex items-center gap-2 cursor-pointer">
          <input
            v-model="decisionChoice"
            type="radio"
            value="needs-revision"
            class="accent-state-awaiting"
          />
          <span>Needs revision</span>
        </label>
      </div>

      <div v-if="decisionChoice === 'needs-revision'" class="space-y-1.5">
        <label for="hil-comment" class="text-xs uppercase tracking-wider text-text-tertiary"
          >Comment <span class="text-state-failed">*</span></label
        >
        <textarea
          id="hil-comment"
          v-model="comment"
          rows="4"
          placeholder="What needs to change? Be specific."
          class="input font-mono text-xs w-full"
        />
      </div>
      <div v-else class="space-y-1.5">
        <label for="hil-comment-opt" class="text-xs uppercase tracking-wider text-text-tertiary"
          >Comment <span class="text-text-tertiary">(optional)</span></label
        >
        <textarea
          id="hil-comment-opt"
          v-model="comment"
          rows="2"
          placeholder="Optional note for the record"
          class="input font-mono text-xs w-full"
        />
      </div>

      <div v-if="error" class="text-state-failed text-xs">{{ error }}</div>

      <div class="flex items-center gap-2 pt-1">
        <button
          type="button"
          class="btn-accent text-sm"
          :disabled="!canSubmit || submit.isPending.value"
          @click="onSubmit"
        >
          {{ submit.isPending.value ? "Submitting…" : submitLabel }}
        </button>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";

import MarkdownView from "@/components/MarkdownView.vue";
import { useNode, useSubmitDecision } from "@/api/queries";

const props = defineProps<{ slug: string; nodeId: string }>();
const slugRef = computed(() => props.slug);
const nodeIdRef = computed<string | null>(() => props.nodeId);

const node = useNode(slugRef, nodeIdRef);
const reviewMd = computed(() => node.data.value?.output ?? "");

const submit = useSubmitDecision(slugRef, toRef(props, "nodeId"));
const decisionChoice = ref<"approved" | "needs-revision">("approved");
const comment = ref("");
const error = ref<string | null>(null);

const submitLabel = computed(() =>
  decisionChoice.value === "approved" ? "Approve" : "Submit revision request",
);

const canSubmit = computed(() => {
  if (decisionChoice.value === "needs-revision") return comment.value.trim().length > 0;
  return true;
});

// Reset comment / error when the node changes (e.g. new awaiting node).
watch(nodeIdRef, () => {
  decisionChoice.value = "approved";
  comment.value = "";
  error.value = null;
});

async function onSubmit(): Promise<void> {
  error.value = null;
  if (decisionChoice.value === "needs-revision" && !comment.value.trim()) {
    error.value = "Please describe what needs to change.";
    return;
  }
  try {
    await submit.mutateAsync({
      decision: decisionChoice.value,
      comment: comment.value.trim() || undefined,
    });
    comment.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>
