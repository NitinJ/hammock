<template>
  <div class="manual-step-form">
    <div class="instructions">
      <p v-for="(line, i) in instructionLines" :key="i">{{ line }}</p>
    </div>

    <textarea
      v-model="output"
      placeholder="Describe what you did / paste relevant output"
      :disabled="submitting"
      rows="5"
      class="output-text"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

interface ManualStepQuestion {
  kind: "manual-step";
  instructions: string;
  extra_fields: Record<string, unknown> | null;
}

const props = defineProps<{ question: ManualStepQuestion; submitting: boolean }>();

const output = ref("");

const instructionLines = computed(() =>
  props.question.instructions.split("\n").filter(Boolean),
);

defineExpose({ getAnswer });

function getAnswer() {
  return { kind: "manual-step" as const, output: output.value, extras: null };
}
</script>
