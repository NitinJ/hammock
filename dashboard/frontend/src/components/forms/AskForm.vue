<template>
  <div class="ask-form">
    <!-- Rendered by FormRenderer for kind="ask" -->
    <p class="question-text">{{ question.text }}</p>

    <div v-if="question.options && question.options.length > 0" class="options">
      <label
        v-for="opt in question.options"
        :key="opt"
        class="option-label"
      >
        <input
          type="radio"
          :value="opt"
          v-model="choice"
          :disabled="submitting"
        />
        {{ opt }}
      </label>
    </div>

    <textarea
      v-model="text"
      :placeholder="question.options ? 'Additional comments (optional)' : 'Your answer'"
      :disabled="submitting"
      rows="4"
      class="answer-text"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";

interface AskQuestion {
  kind: "ask";
  text: string;
  options: string[] | null;
}

const props = defineProps<{ question: AskQuestion; submitting: boolean }>();
const emit = defineEmits<{
  (e: "answer", payload: { kind: "ask"; choice: string | null; text: string }): void;
}>();

const choice = ref<string | null>(null);
const text = ref("");

defineExpose({ getAnswer });

function getAnswer() {
  return { kind: "ask" as const, choice: choice.value, text: text.value };
}
</script>
