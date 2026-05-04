import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import Settings from "@/views/Settings.vue";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

interface SettingsResponse {
  runner_mode: string;
  claude_binary: string | null;
  cache_size: number;
  active_jobs: Array<{
    job_slug: string;
    state: string;
    heartbeat_age_seconds: number | null;
    pid: number | null;
    pid_alive: boolean;
  }>;
  projects: Array<{
    slug: string;
    doctor_status: string | null;
    last_health_check_at: string | null;
  }>;
  inventory: {
    agents_per_project: Record<string, number>;
    skills_per_project: Record<string, number>;
    total_agent_overrides: number;
    total_skill_overrides: number;
  };
  mcp_server_count: number;
}

const baseMock: SettingsResponse = {
  runner_mode: "fake",
  claude_binary: null,
  cache_size: 42,
  active_jobs: [],
  projects: [],
  inventory: {
    agents_per_project: {},
    skills_per_project: {},
    total_agent_overrides: 0,
    total_skill_overrides: 0,
  },
  mcp_server_count: 0,
};

function mountWith(data: SettingsResponse) {
  const qc = makeClient();
  qc.setQueryData(["settings"], data);
  return mount(Settings, {
    global: {
      plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
      stubs: { RouterLink: { template: "<a><slot /></a>" } },
    },
  });
}

describe("Settings", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders runner mode and cache size", async () => {
    const wrapper = mountWith(baseMock);
    await nextTick();
    expect(wrapper.text()).toContain("fake");
    expect(wrapper.text()).toContain("42");
  });

  it("shows Settings heading", async () => {
    const wrapper = mountWith(baseMock);
    await nextTick();
    expect(wrapper.text()).toContain("Settings");
  });

  it("renders MCP server count", async () => {
    const wrapper = mountWith({ ...baseMock, mcp_server_count: 3 });
    await nextTick();
    expect(wrapper.text()).toContain("MCP servers");
    expect(wrapper.text()).toContain("3");
  });

  it("lists active jobs with heartbeat + pid", async () => {
    const wrapper = mountWith({
      ...baseMock,
      active_jobs: [
        {
          job_slug: "j-running",
          state: "STAGES_RUNNING",
          heartbeat_age_seconds: 15,
          pid: 4242,
          pid_alive: true,
        },
      ],
    });
    await nextTick();
    expect(wrapper.text()).toContain("Active jobs");
    expect(wrapper.text()).toContain("j-running");
    expect(wrapper.text()).toContain("STAGES_RUNNING");
    expect(wrapper.text()).toContain("4242");
    expect(wrapper.text()).toContain("alive");
  });

  it("lists projects with doctor + override counts", async () => {
    const wrapper = mountWith({
      ...baseMock,
      projects: [{ slug: "alpha", doctor_status: "pass", last_health_check_at: null }],
      inventory: {
        agents_per_project: { alpha: 2 },
        skills_per_project: { alpha: 1 },
        total_agent_overrides: 2,
        total_skill_overrides: 1,
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("Projects");
    expect(wrapper.text()).toContain("alpha");
    expect(wrapper.text()).toContain("pass");
    expect(wrapper.text()).toContain("Total overrides: 2 agents, 1 skills");
  });
});
