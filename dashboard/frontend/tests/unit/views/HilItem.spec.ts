import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createWebHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import HilItem from "@/views/HilItem.vue";
import type { UiTemplate } from "@/components/forms/TemplateRegistry";

const askDetail = {
  item: {
    id: "hil-ask-1",
    kind: "ask",
    status: "awaiting",
    question: { kind: "ask", text: "Use Argon2id?", options: ["yes", "no"] },
    stage_id: "s1",
    created_at: "2026-05-01T12:00:00Z",
    answered_at: null,
    answer: null,
    task_id: null,
  },
  job_slug: "alpha-job-1",
  project_slug: "alpha",
  ui_template_name: "ask-default-form",
};

const askTemplate: UiTemplate = {
  name: "ask-default-form",
  description: null,
  hil_kinds: ["ask"],
  instructions: "Please answer the question.",
  fields: { submit_label: "Submit Answer" },
};

const answeredDetail = {
  ...askDetail,
  item: { ...askDetail.item, status: "answered" },
};

function makeRouter(itemId = "hil-ask-1") {
  const router = createRouter({
    history: createWebHistory(),
    routes: [{ path: "/hil/:itemId", component: HilItem }],
  });
  router.push(`/hil/${itemId}`);
  return router;
}

function mockFetch(responses: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      for (const [pattern, body] of Object.entries(responses)) {
        if (url.includes(pattern)) {
          return {
            ok: true,
            status: 200,
            json: () => Promise.resolve(body),
          };
        }
      }
      return { ok: false, status: 404, json: () => Promise.resolve({ detail: "not found" }) };
    }),
  );
}

describe("HilItem", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows loading state initially", async () => {
    mockFetch({ "/api/hil/hil-ask-1": askDetail, "/api/hil/templates": askTemplate });
    const router = makeRouter();
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    expect(w.text()).toContain("Loading");
  });

  it("renders the form after data loads", async () => {
    mockFetch({ "/api/hil/hil-ask-1": askDetail, "/api/hil/templates": askTemplate });
    const router = makeRouter();
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();
    expect(w.find(".ask-form").exists()).toBe(true);
    expect(w.text()).toContain("Answer Required");
  });

  it("shows 'already answered' message for non-awaiting items", async () => {
    mockFetch({ "/api/hil/hil-ask-1": answeredDetail, "/api/hil/templates": askTemplate });
    const router = makeRouter();
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();
    expect(w.text()).toContain("already answered");
    expect(w.find(".ask-form").exists()).toBe(false);
  });

  it("shows fetch error on 404", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404, json: () => Promise.resolve({}) })),
    );
    const router = makeRouter("no-such-item");
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();
    expect(w.find("[role='alert']").exists()).toBe(true);
  });

  it("calls POST /api/hil/{id}/answer on form submit", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      if (url.includes("/api/hil/hil-ask-1/answer")) {
        return {
          ok: true,
          status: 200,
          json: () => Promise.resolve({ ...askDetail.item, status: "answered" }),
        };
      }
      if (url.includes("/api/hil/templates")) {
        return { ok: true, status: 200, json: () => Promise.resolve(askTemplate) };
      }
      return { ok: true, status: 200, json: () => Promise.resolve(askDetail) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const router = makeRouter();
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();

    // Trigger submit through FormRenderer
    const formRenderer = w.findComponent({ name: "FormRenderer" });
    await formRenderer.vm.$emit("submit", { kind: "ask", choice: "yes", text: "Yes." });
    await flushPromises();

    const postCall = fetchMock.mock.calls.find(
      ([url, opts]) => url.includes("/answer") && opts?.method === "POST",
    );
    expect(postCall).toBeTruthy();
  });

  it("shows submit error on POST failure", async () => {
    const fetchMock = vi.fn(async (url: string, opts?: RequestInit) => {
      if (url.includes("/answer")) {
        return {
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: "Conflict error" }),
        };
      }
      if (url.includes("/templates")) {
        return { ok: true, status: 200, json: () => Promise.resolve(askTemplate) };
      }
      return { ok: true, status: 200, json: () => Promise.resolve(askDetail) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const router = makeRouter();
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();

    const formRenderer = w.findComponent({ name: "FormRenderer" });
    await formRenderer.vm.$emit("submit", { kind: "ask", choice: "yes", text: "test" });
    await flushPromises();

    expect(w.text()).toContain("Conflict error");
  });

  it("shows error when template cannot be found (404)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/templates")) {
          return { ok: false, status: 404, json: () => Promise.resolve({}) };
        }
        return { ok: true, status: 200, json: () => Promise.resolve(askDetail) };
      }),
    );
    const router = makeRouter();
    await router.isReady();
    const w = mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();
    expect(w.find("[role='alert']").exists()).toBe(true);
  });

  it("forwards project_slug to /api/hil/templates so per-project overrides resolve", async () => {
    // v0 alignment report Plan #10: when the HIL detail names a project,
    // the template fetch must include `?project_slug=<slug>` so the
    // backend resolver picks the project's `<repo>/.hammock/ui-templates/`
    // override before falling back to the bundled default.
    const calls: string[] = [];
    const fetchMock = vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("/templates/")) {
        return {
          ok: true,
          status: 200,
          json: () => Promise.resolve(askTemplate),
        };
      }
      return { ok: true, status: 200, json: () => Promise.resolve(askDetail) };
    });
    vi.stubGlobal("fetch", fetchMock);

    const router = makeRouter();
    await router.isReady();
    mount(HilItem, { global: { plugins: [router, createPinia()] } });
    await flushPromises();

    const templateCall = calls.find((u) => u.includes("/templates/"));
    expect(templateCall).toBeDefined();
    // askDetail.project_slug === "alpha" → must appear in the query string
    expect(templateCall).toContain("project_slug=alpha");
  });
});
