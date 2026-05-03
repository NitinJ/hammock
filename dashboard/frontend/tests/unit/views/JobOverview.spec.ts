import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { JobDetail } from "@/api/schema.d";
import JobOverview from "@/views/JobOverview.vue";

vi.mock("@/sse", () => ({
  useEventStream: vi.fn(() => ({
    connected: ref(false),
    lastSeq: ref<number | null>(null),
    error: ref<string | null>(null),
    close: vi.fn(),
  })),
}));

vi.mock("vue-router", async () => {
  const actual = await vi.importActual<typeof import("vue-router")>("vue-router");
  return {
    ...actual,
    useRoute: vi.fn(() => ({ params: { jobSlug: "feat-auth-20260501" } })),
  };
});

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

const mockJobDetail: JobDetail = {
  job: {
    job_id: "job-id-1",
    job_slug: "feat-auth-20260501",
    project_slug: "my-project",
    job_type: "build-feature",
    created_at: "2026-05-01T08:00:00Z",
    created_by: "human",
    state: "STAGES_RUNNING",
  },
  stages: [
    {
      stage_id: "design",
      state: "SUCCEEDED",
      attempt: 1,
      started_at: "2026-05-01T08:01:00Z",
      ended_at: "2026-05-01T08:30:00Z",
      cost_accrued: 0.12,
    },
    {
      stage_id: "implement",
      state: "RUNNING",
      attempt: 1,
      started_at: "2026-05-01T08:31:00Z",
      ended_at: null,
      cost_accrued: 0.35,
    },
  ],
  total_cost_usd: 0.47,
};

describe("JobOverview", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders job slug in header", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.job("feat-auth-20260501"), mockJobDetail);
    const wrapper = mount(JobOverview, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("feat-auth-20260501");
  });

  it("renders stage ids in timeline", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.job("feat-auth-20260501"), mockJobDetail);
    const wrapper = mount(JobOverview, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("design");
    expect(wrapper.text()).toContain("implement");
  });

  it("renders total cost", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.job("feat-auth-20260501"), mockJobDetail);
    const wrapper = mount(JobOverview, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("0.47");
  });

  it("shows job state badge", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.job("feat-auth-20260501"), mockJobDetail);
    const wrapper = mount(JobOverview, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    // StateBadge renders STAGES_RUNNING as "Running"
    expect(wrapper.text()).toContain("Running");
  });
});
