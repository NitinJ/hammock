import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { ref } from "vue";
import { QueryClient, VueQueryPlugin } from "@tanstack/vue-query";
import { setActivePinia, createPinia } from "pinia";
import { QUERY_KEYS } from "@/api/queries";
import ArtifactViewer from "@/views/ArtifactViewer.vue";

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
    useRoute: vi.fn(() => ({
      params: {
        jobSlug: "feat-auth-20260501",
        path: ["design-spec.md"],
      },
    })),
  };
});

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

const markdownContent = "# Design Spec\n\nThis is the design specification.";

describe("ArtifactViewer", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders artifact content in markdown view", async () => {
    const qc = makeClient();
    qc.setQueryData(
      QUERY_KEYS.artifact("feat-auth-20260501", "design-spec.md"),
      markdownContent,
    );
    const wrapper = mount(ArtifactViewer, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: {
          RouterLink: { template: "<a><slot /></a>" },
          MarkdownView: { template: '<div class="md">{{ content }}</div>', props: ["content"] },
        },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("Design Spec");
  });

  it("renders artifact path in header", async () => {
    const qc = makeClient();
    qc.setQueryData(
      QUERY_KEYS.artifact("feat-auth-20260501", "design-spec.md"),
      markdownContent,
    );
    const wrapper = mount(ArtifactViewer, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: {
          RouterLink: { template: "<a><slot /></a>" },
          MarkdownView: { template: '<div class="md">{{ content }}</div>', props: ["content"] },
        },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("design-spec.md");
  });

  it("shows loading state while fetching", async () => {
    const qc = makeClient();
    const wrapper = mount(ArtifactViewer, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: qc }], createPinia()],
        stubs: {
          RouterLink: { template: "<a><slot /></a>" },
          MarkdownView: { template: '<div class="md">{{ content }}</div>', props: ["content"] },
        },
      },
    });
    await nextTick();
    expect(wrapper.text()).toContain("Loading");
  });
});
