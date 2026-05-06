<template>
  <div class="space-y-1">
    <label class="block text-xs uppercase text-text-secondary">{{ name }}</label>
    <input
      type="text"
      :value="modelValue ?? ''"
      class="block w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary placeholder-text-secondary focus:border-blue-500 focus:outline-none"
      :placeholder="placeholder"
      @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    />
  </div>
</template>

<script setup lang="ts">
defineProps<{
  name: string;
  modelValue: string | null;
  placeholder?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
</script>
