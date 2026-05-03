<template>
  <div class="form-renderer">
    <!-- Template instructions / context -->
    <div v-if="template?.instructions" class="template-instructions">
      <p>{{ template.instructions }}</p>
    </div>
    <div v-if="template?.fields?.extra_help" class="extra-help">
      <p>{{ template.fields.extra_help }}</p>
    </div>

    <!-- Kind-specific form -->
    <AskForm
      v-if="item.item.kind === 'ask'"
      ref="formRef"
      :question="item.item.question as AskQuestion"
      :submitting="submitting"
    />
    <ReviewForm
      v-else-if="item.item.kind === 'review'"
      ref="formRef"
      :question="item.item.question as ReviewQuestion"
      :submitting="submitting"
    />
    <ManualStepForm
      v-else-if="item.item.kind === 'manual-step'"
      ref="formRef"
      :question="item.item.question as ManualStepQuestion"
      :submitting="submitting"
    />

    <!-- Validation + submit errors -->
    <p v-if="validationError" class="submit-error" role="alert">{{ validationError }}</p>
    <p v-else-if="error" class="submit-error" role="alert">{{ error }}</p>

    <!-- Submit -->
    <button
      class="btn-submit"
      :disabled="submitting"
      @click="handleSubmit"
      type="button"
    >
      {{ submitLabel }}
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import AskForm from "./AskForm.vue";
import ReviewForm from "./ReviewForm.vue";
import ManualStepForm from "./ManualStepForm.vue";
import type { UiTemplate } from "./TemplateRegistry";

interface AskQuestion { kind: "ask"; text: string; options: string[] | null }
interface ReviewQuestion { kind: "review"; target: string; prompt: string }
interface ManualStepQuestion { kind: "manual-step"; instructions: string; extra_fields: Record<string, unknown> | null }

interface HilItemDetail {
  item: { kind: string; question: unknown; [k: string]: unknown };
  job_slug: string | null;
  project_slug: string | null;
  ui_template_name: string;
}

const props = defineProps<{
  item: HilItemDetail;
  template: UiTemplate | null;
  submitting: boolean;
  error: string | null;
}>();

const emit = defineEmits<{
  (e: "submit", answer: unknown): void;
}>();

const formRef = ref<{ getAnswer(): unknown } | null>(null);
const validationError = ref<string | null>(null);

const submitLabel = computed(
  () => props.template?.fields?.submit_label ?? "Submit",
);

function handleSubmit() {
  validationError.value = null;
  const answer = formRef.value?.getAnswer() as Record<string, unknown> | undefined;
  if (answer === undefined) return;
  if (answer.kind === "review" && answer.decision == null) {
    validationError.value = "Please select a decision before submitting.";
    return;
  }
  emit("submit", answer);
}
</script>
