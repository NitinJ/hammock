<template>
  <div>
    <p v-if="!stages.length">No stages</p>
    <ul v-else>
      <li v-for="s in stages" :key="s.stage_id">
        <RouterLink :to="`/jobs/${jobSlug}/stages/${s.stage_id}`">{{ s.stage_id }}</RouterLink>
        {{ s.state }} ${{ s.cost_accrued.toFixed(2) }}
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import type { StageListEntry } from "@/api/schema.d";

defineProps<{ stages: StageListEntry[]; jobSlug: string }>();
</script>
