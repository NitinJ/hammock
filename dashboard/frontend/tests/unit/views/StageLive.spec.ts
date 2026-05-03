import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  closed = false;
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  close(): void { this.closed = true; }
}

vi.stubGlobal("EventSource", MockEventSource);

const mockStageDetail = {
  job_slug: "my-job",
  stage: {
    stage_id: "implement",
    attempt: 1,
    state: "RUNNING",
    started_at: "2026-05-01T12:00:00Z",
    ended_at: null,
    cost_accrued: 1.5,
    restart_count: 0,
  },
  tasks: [
    { task_id: "t1", stage_id: "implement", state: "RUNNING", created_at: "2026-05-01T12:01:00Z" },
  ],
};

vi.stubGlobal(
  "fetch",
  vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/api/jobs/my-job/stages/implement")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockStageDetail),
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  }),
);

import StageLive from "@/views/StageLive.vue";

describe("StageLive", () => {
  let router: ReturnType<typeof createRouter>;

  beforeEach(() => {
    setActivePinia(createPinia());
    MockEventSource.instances.length = 0;
    router = createRouter({
      history: createWebHistory(),
      routes: [
        {
          path: "/jobs/:jobSlug/stages/:stageId",
          component: StageLive,
          name: "stage-live",
        },
        {
          path: "/jobs/:jobSlug",
          component: { template: "<div />" },
          name: "job-overview",
        },
      ],
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders three-pane layout", async () => {
    router.push("/jobs/my-job/stages/implement");
    await router.isReady();
    const wrapper = mount(StageLive, {
      global: { plugins: [router] },
    });
    await wrapper.vm.$nextTick();
    // Three panes: left, centre, right
    expect(wrapper.find("[data-pane='left']").exists()).toBe(true);
    expect(wrapper.find("[data-pane='centre']").exists()).toBe(true);
    expect(wrapper.find("[data-pane='right']").exists()).toBe(true);
  });

  it("renders stage id and job slug in header", async () => {
    router.push("/jobs/my-job/stages/implement");
    await router.isReady();
    const wrapper = mount(StageLive, {
      global: { plugins: [router] },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toMatch(/my-job|implement/);
  });

  it("opens SSE for the correct scope", async () => {
    router.push("/jobs/my-job/stages/implement");
    await router.isReady();
    mount(StageLive, {
      global: { plugins: [router] },
    });
    await new Promise((r) => setTimeout(r, 0));
    const urls = MockEventSource.instances.map((s) => s.url);
    expect(urls.some((u) => u.includes("stage/my-job/implement"))).toBe(true);
  });
});
