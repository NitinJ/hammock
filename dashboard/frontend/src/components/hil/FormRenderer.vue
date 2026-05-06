<template>
  <form class="space-y-4" @submit.prevent="handleSubmit">
    <div v-if="fields.length === 0" class="text-text-secondary">
      Variable type <code class="rounded bg-surface-highlight px-1">{{ typeName }}</code> has no
      form schema. Cannot submit via the dashboard.
    </div>

    <component
      :is="widgetFor(widget)"
      v-for="[name, widget] in fields"
      :key="name"
      :name="name"
      :widget-type="widget"
      :model-value="(values[name] as string | null) ?? null"
      :placeholder="widget === 'textarea' ? 'Type here…' : ''"
      @update:model-value="(value: string) => (values[name] = value)"
    />

    <div class="flex items-center gap-3 pt-2">
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
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import Select from "./widgets/Select.vue";
import Textarea from "./widgets/Textarea.vue";
import Text from "./widgets/Text.vue";

const props = defineProps<{
  /** Variable type name, e.g. ``"pr-review-verdict"``. Display only. */
  typeName: string;
  /** ``[(field_name, widget_type), ...]`` from HilQueueItem.form_schemas. */
  fields: [string, string][];
  /** Async submitter — receives ``{var_name → typed payload}``. */
  onSubmit: (value: Record<string, string>) => Promise<void>;
}>();

const values = reactive<Record<string, string | null>>({});
for (const [name] of props.fields) values[name] = null;

const canSubmit = computed(() => {
  // Required: every declared field has a non-empty string.
  for (const [name] of props.fields) {
    const v = values[name];
    if (v === null || v === undefined || v === "") return false;
  }
  return true;
});

const submitting = ref(false);
const error = ref<string | null>(null);

function widgetFor(widget: string) {
  if (widget.startsWith("select:")) return Select;
  if (widget === "textarea") return Textarea;
  return Text;
}

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return;
  submitting.value = true;
  error.value = null;
  try {
    const payload: Record<string, string> = {};
    for (const [name] of props.fields) payload[name] = values[name] ?? "";
    await props.onSubmit(payload);
  } catch (e) {
    error.value = (e as Error).message ?? String(e);
  } finally {
    submitting.value = false;
  }
}
</script>
