import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { ActiveStageStripItem, HilQueueItem, JobListItem } from "@/api/schema.d";
import Home from "@/views/Home.vue";

vi.mock("@/sse", () => ({
  useEventStream: vi.fn(() => ({
    connected: ref(false),
    lastSeq: ref<number | null>(null),
    error: ref<string | null>(null),
    close: vi.fn(),
  })),
}));

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

const mockActiveStage: ActiveStageStripItem = {
  project_slug: "proj-a",
  job_slug: "job-abc",
  stage_id: "stage-01",
  state: "RUNNING",
  cost_accrued: 0.42,
  started_at: "2026-05-01T09:00:00Z",
};

const mockHilItem: HilQueueItem = {
  item_id: "hil-1",
  kind: "ask",
  status: "awaiting",
  stage_id: "stage-01",
  job_slug: "job-abc",
  project_slug: "proj-a",
  created_at: "2026-05-01T09:00:00Z",
  age_seconds: 120,
};

const mockJob: JobListItem = {
  job_id: "job-id-1",
  job_slug: "job-abc",
  project_slug: "proj-a",
  job_type: "build-feature",
  state: "STAGES_RUNNING",
  created_at: "2026-05-01T08:00:00Z",
  total_cost_usd: 0.42,
  current_stage_id: "stage-01",
};

describe("Home", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders active stage card with job slug", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.activeStages, [mockActiveStage]);
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), []);
    qc.setQueryData(QUERY_KEYS.jobs(null), []);
    const wrapper = mount(Home, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("job-abc");
  });

  it("renders HIL awaiting item", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.activeStages, []);
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), [mockHilItem]);
    qc.setQueryData(QUERY_KEYS.jobs(null), []);
    const wrapper = mount(Home, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("hil-1");
  });

  it("renders recent jobs", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.activeStages, []);
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), []);
    qc.setQueryData(QUERY_KEYS.jobs(null), [mockJob]);
    const wrapper = mount(Home, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("job-abc");
  });

  it("shows empty state when nothing is active", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.activeStages, []);
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), []);
    qc.setQueryData(QUERY_KEYS.jobs(null), []);
    const wrapper = mount(Home, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("No active stages");
  });
});
