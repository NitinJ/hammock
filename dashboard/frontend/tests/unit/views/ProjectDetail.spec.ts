import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { ProjectDetail, JobListItem } from "@/api/schema.d";
import ProjectDetailView from "@/views/ProjectDetail.vue";

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
    useRoute: vi.fn(() => ({ params: { slug: "my-project" } })),
  };
});

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

const mockDetail: ProjectDetail = {
  project: {
    slug: "my-project",
    name: "My Project",
    repo_path: "/repos/my-project",
    remote_url: "https://github.com/org/my-project",
    default_branch: "main",
    created_at: "2026-01-01T00:00:00Z",
    last_health_check_at: null,
    last_health_check_status: null,
  },
  total_jobs: 3,
  open_hil_count: 1,
  jobs_by_state: { COMPLETED: 2, STAGES_RUNNING: 1 },
};

const mockJob: JobListItem = {
  job_id: "job-id-1",
  job_slug: "my-project-job-1",
  project_slug: "my-project",
  job_type: "build-feature",
  state: "COMPLETED",
  created_at: "2026-05-01T00:00:00Z",
  total_cost_usd: 1.23,
  current_stage_id: null,
};

describe("ProjectDetail", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders project name from query data", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.project("my-project"), mockDetail);
    qc.setQueryData(QUERY_KEYS.jobs("my-project"), [mockJob]);
    const wrapper = mount(ProjectDetailView, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("My Project");
  });

  it("renders repo path", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.project("my-project"), mockDetail);
    qc.setQueryData(QUERY_KEYS.jobs("my-project"), []);
    const wrapper = mount(ProjectDetailView, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("/repos/my-project");
  });

  it("renders job list for the project", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.project("my-project"), mockDetail);
    qc.setQueryData(QUERY_KEYS.jobs("my-project"), [mockJob]);
    const wrapper = mount(ProjectDetailView, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("my-project-job-1");
  });

  it("shows open HIL count", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.project("my-project"), mockDetail);
    qc.setQueryData(QUERY_KEYS.jobs("my-project"), []);
    const wrapper = mount(ProjectDetailView, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("1");
  });
});
