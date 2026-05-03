import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { HilQueueItem } from "@/api/schema.d";
import HilQueue from "@/views/HilQueue.vue";

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

const mockHilItem: HilQueueItem = {
  item_id: "hil-abc-123",
  kind: "ask",
  status: "awaiting",
  stage_id: "design",
  job_slug: "feat-auth-20260501",
  project_slug: "my-project",
  created_at: "2026-05-01T10:00:00Z",
  age_seconds: 300,
};

const mockReviewItem: HilQueueItem = {
  item_id: "hil-rev-456",
  kind: "review",
  status: "awaiting",
  stage_id: "review",
  job_slug: "fix-login-20260501",
  project_slug: "my-project",
  created_at: "2026-05-01T11:00:00Z",
  age_seconds: 60,
};

describe("HilQueue", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders HIL item id", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), [mockHilItem]);
    const wrapper = mount(HilQueue, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("hil-abc-123");
  });

  it("renders HIL kind badge", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), [mockHilItem]);
    const wrapper = mount(HilQueue, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("ask");
  });

  it("renders job slug for each item", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), [mockHilItem, mockReviewItem]);
    const wrapper = mount(HilQueue, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("feat-auth-20260501");
    expect(wrapper.text()).toContain("fix-login-20260501");
  });

  it("shows empty state when no awaiting items", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), []);
    const wrapper = mount(HilQueue, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("No awaiting items");
  });

  it("renders age in seconds", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.hil("awaiting"), [mockHilItem]);
    const wrapper = mount(HilQueue, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("300");
  });
});
