<template>
  <div class="review-form">
    <p class="review-prompt">{{ question.prompt }}</p>
    <p v-if="question.target" class="review-target">Artifact: {{ question.target }}</p>

    <div class="decision-buttons">
      <button
        :class="['btn-approve', { active: decision === 'approve' }]"
        :disabled="submitting"
        @click="decision = 'approve'"
        type="button"
      >
        Approve
      </button>
      <button
        :class="['btn-reject', { active: decision === 'reject' }]"
        :disabled="submitting"
        @click="decision = 'reject'"
        type="button"
      >
        Reject
      </button>
    </div>

    <textarea
      v-model="comments"
      placeholder="Comments (required for reject, optional for approve)"
      :disabled="submitting"
      rows="5"
      class="review-comments"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

interface ReviewQuestion {
  kind: "review";
  target: string;
  prompt: string;
}

const props = defineProps<{ question: ReviewQuestion; submitting: boolean }>();

const decision = ref<"approve" | "reject" | null>(null);
const comments = ref("");

defineExpose({ getAnswer });

function getAnswer() {
  return {
    kind: "review" as const,
    decision: decision.value ?? "approve",
    comments: comments.value,
  };
}
</script>
