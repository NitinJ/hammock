import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { CostRollup } from "@/api/schema.d";
import CostDashboard from "@/views/CostDashboard.vue";

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
    useRoute: vi.fn(() => ({ query: { scope: "job", id: "feat-auth-20260501" } })),
  };
});

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

const mockRollup: CostRollup = {
  scope: "job",
  id: "feat-auth-20260501",
  total_usd: 2.47,
  total_tokens: 12500,
  by_stage: { design: 0.80, implement: 1.67 },
  by_agent: { "agent0": 1.20, "subagent-1": 1.27 },
};

describe("CostDashboard", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders total cost from query data", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.costs("job", "feat-auth-20260501"), mockRollup);
    const wrapper = mount(CostDashboard, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("2.47");
  });

  it("renders scope label", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.costs("job", "feat-auth-20260501"), mockRollup);
    const wrapper = mount(CostDashboard, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("job");
  });

  it("renders per-stage breakdown entries", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.costs("job", "feat-auth-20260501"), mockRollup);
    const wrapper = mount(CostDashboard, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("design");
    expect(wrapper.text()).toContain("implement");
  });

  it("renders total tokens", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.costs("job", "feat-auth-20260501"), mockRollup);
    const wrapper = mount(CostDashboard, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("12500");
  });
});
