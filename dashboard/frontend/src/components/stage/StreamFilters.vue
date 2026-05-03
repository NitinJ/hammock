<template>
  <div class="flex gap-3 text-xs text-text-secondary">
    <label class="flex items-center gap-1">
      <input
        type="checkbox"
        :checked="modelValue.hideToolCalls"
        @change="update('hideToolCalls', ($event.target as HTMLInputElement).checked)"
      />
      Hide tools
    </label>
    <label class="flex items-center gap-1">
      <input
        type="checkbox"
        :checked="modelValue.hideEngineNudges"
        @change="update('hideEngineNudges', ($event.target as HTMLInputElement).checked)"
      />
      Hide nudges
    </label>
    <label class="flex items-center gap-1">
      <input
        type="checkbox"
        :checked="modelValue.proseOnly"
        @change="update('proseOnly', ($event.target as HTMLInputElement).checked)"
      />
      Prose only
    </label>
  </div>
</template>

<script setup lang="ts">
import type { StreamFilters } from "@/composables/useAgent0Stream";

const props = defineProps<{ modelValue: StreamFilters }>();
const emit = defineEmits<{ (e: "update:modelValue", v: StreamFilters): void }>();

function update(key: keyof StreamFilters, value: boolean): void {
  emit("update:modelValue", { ...props.modelValue, [key]: value });
}
</script>
