import { defineStore } from "pinia";
import { ref } from "vue";
import type { JobListItem, JobState } from "@/api/schema.d";

export const useJobsStore = defineStore("jobs", () => {
  const items = ref<JobListItem[]>([]);

  function setJobs(list: JobListItem[]): void {
    items.value = list;
  }

  function patchJobState(jobSlug: string, state: JobState): void {
    const job = items.value.find((j) => j.job_slug === jobSlug);
    if (job) {
      job.state = state;
    }
  }

  function patchJobCost(jobSlug: string, costUsd: number): void {
    const job = items.value.find((j) => j.job_slug === jobSlug);
    if (job) {
      job.total_cost_usd = costUsd;
    }
  }

  return { items, setJobs, patchJobState, patchJobCost };
});
