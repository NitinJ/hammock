<template>
  <div class="space-y-2">
    <label
      v-for="option in options"
      :key="option.value"
      class="flex items-start gap-3 cursor-pointer"
    >
      <input
        type="radio"
        :value="option.value"
        :checked="modelValue === option.value"
        class="mt-0.5 accent-blue-500"
        @change="$emit('update:modelValue', option.value)"
      />
      <span class="font-medium text-text-primary">{{ option.label }}</span>
    </label>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  modelValue: string;
}>();

defineEmits<{
  "update:modelValue": [value: string];
}>();

const options = [
  { value: "build-feature", label: "Build feature" },
  { value: "fix-bug", label: "Fix bug" },
];
</script>
