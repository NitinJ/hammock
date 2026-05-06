<template>
  <div class="space-y-1">
    <label class="block text-xs uppercase text-text-secondary">{{ name }}</label>
    <div class="flex flex-wrap gap-2">
      <button
        v-for="opt in options"
        :key="opt"
        type="button"
        :class="[
          'rounded-md border px-3 py-1.5 text-sm transition-colors',
          modelValue === opt
            ? 'border-blue-500 bg-blue-500/20 text-blue-200'
            : 'border-border bg-surface-raised text-text-secondary hover:bg-surface-highlight',
        ]"
        @click="emit('update:modelValue', opt)"
      >
        {{ opt }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  name: string;
  /** Widget type from backend, e.g. ``"select:merged,needs-revision"``. */
  widgetType: string;
  modelValue: string | null;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();

const options = computed(() => {
  const tail = props.widgetType.startsWith("select:")
    ? props.widgetType.slice("select:".length)
    : "";
  return tail
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
});
</script>
