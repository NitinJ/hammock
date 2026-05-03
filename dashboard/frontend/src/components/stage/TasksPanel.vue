<template>
  <div class="text-sm">
    <p v-if="tasks.length === 0" class="text-text-secondary italic">No tasks</p>
    <ul v-else class="space-y-1">
      <li v-for="task in tasks" :key="task.task_id" class="flex items-center gap-2">
        <StateBadge :state="task.state" />
        <span class="font-mono text-xs">{{ task.task_id }}</span>
        <span v-if="task.subagent_id" class="text-text-secondary truncate text-xs">→ {{ task.subagent_id }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import StateBadge from "@/components/shared/StateBadge.vue";
import type { TaskRecord } from "@/api/schema.d";

defineProps<{ tasks: TaskRecord[] }>();
</script>
