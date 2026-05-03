import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { ProjectListItem } from "@/api/schema.d";
import ProjectList from "@/views/ProjectList.vue";

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

const mockProject: ProjectListItem = {
  slug: "alpha-proj",
  name: "Alpha Project",
  repo_path: "/repos/alpha",
  default_branch: "main",
  total_jobs: 5,
  open_hil_count: 2,
  last_job_at: "2026-05-01T10:00:00Z",
  doctor_status: "green",
};

describe("ProjectList", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders project name from query data", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.projects, [mockProject]);
    const wrapper = mount(ProjectList, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("Alpha Project");
  });

  it("renders doctor status badge", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.projects, [mockProject]);
    const wrapper = mount(ProjectList, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("green");
  });

  it("shows empty state when no projects", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.projects, []);
    const wrapper = mount(ProjectList, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("No projects");
  });

  it("shows loading state when query is pending", async () => {
    const qc = makeClient();
    const wrapper = mount(ProjectList, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("Loading");
  });

  it("renders open HIL count per project", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.projects, [{ ...mockProject, open_hil_count: 3 }]);
    const wrapper = mount(ProjectList, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("3");
  });
});
