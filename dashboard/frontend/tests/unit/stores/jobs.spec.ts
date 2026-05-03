import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useJobsStore } from "@/stores/jobs";
import type { JobListItem } from "@/api/schema.d";

function makeJob(overrides: Partial<JobListItem> = {}): JobListItem {
  return {
    job_id: "job-id-1",
    job_slug: "test-job-1",
    project_slug: "my-project",
    job_type: "build-feature",
    state: "SUBMITTED",
    created_at: "2026-05-02T10:00:00Z",
    total_cost_usd: 0,
    current_stage_id: null,
    ...overrides,
  };
}

describe("useJobsStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("starts empty", () => {
    const store = useJobsStore();
    expect(store.items).toHaveLength(0);
  });

  it("setJobs replaces the list", () => {
    const store = useJobsStore();
    store.setJobs([makeJob(), makeJob({ job_slug: "test-job-2" })]);
    expect(store.items).toHaveLength(2);
  });

  it("patchJobState updates a job's state", () => {
    const store = useJobsStore();
    store.setJobs([makeJob()]);
    store.patchJobState("test-job-1", "STAGES_RUNNING");
    expect(store.items[0]?.state).toBe("STAGES_RUNNING");
  });

  it("patchJobState is a no-op for unknown slug", () => {
    const store = useJobsStore();
    store.setJobs([makeJob()]);
    store.patchJobState("nonexistent", "COMPLETED");
    expect(store.items[0]?.state).toBe("SUBMITTED");
  });

  it("patchJobCost updates a job's cost", () => {
    const store = useJobsStore();
    store.setJobs([makeJob()]);
    store.patchJobCost("test-job-1", 3.14);
    expect(store.items[0]?.total_cost_usd).toBe(3.14);
  });
});
