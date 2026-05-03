import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import type { HealthResponse } from "@/api/schema.d";
import Settings from "@/views/Settings.vue";

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

const mockHealth: HealthResponse = {
  ok: true,
  cache_size: 42,
};

describe("Settings", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders server health status", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.health, mockHealth);
    const wrapper = mount(Settings, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("ok");
  });

  it("renders cache size from health response", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.health, mockHealth);
    const wrapper = mount(Settings, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("42");
  });

  it("shows Settings heading", async () => {
    const qc = makeClient();
    qc.setQueryData(QUERY_KEYS.health, mockHealth);
    const wrapper = mount(Settings, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("Settings");
  });
});
