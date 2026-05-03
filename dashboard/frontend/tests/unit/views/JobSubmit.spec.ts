import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createRouter, createMemoryHistory } from "vue-router";
import { createPinia, setActivePinia } from "pinia";
import JobSubmit from "@/views/JobSubmit.vue";

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const fakeProjects = [
  {
    slug: "alpha",
    name: "alpha",
    repo_path: "/tmp/alpha",
    default_branch: "main",
    total_jobs: 1,
    open_hil_count: 0,
    last_job_at: null,
    doctor_status: "pass",
  },
  {
    slug: "beta",
    name: "beta",
    repo_path: "/tmp/beta",
    default_branch: "main",
    total_jobs: 0,
    open_hil_count: 0,
    last_job_at: null,
    doctor_status: "pass",
  },
];

const fakeSuccessResponse = {
  job_slug: "2026-05-03-fix-login-crash",
  dry_run: false,
  stages: null,
};

const fakeDryRunResponse = {
  job_slug: "2026-05-03-fix-login-crash",
  dry_run: true,
  stages: [
    { id: "write-bug-report", description: "Write bug report" },
    { id: "write-design-spec", description: "Write design spec" },
  ],
};

const fakeCompileErrors = {
  detail: [{ kind: "project_not_found", stage_id: null, message: "no project" }],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory("/jobs/new"),
    routes: [
      { path: "/jobs/new", component: JobSubmit },
      { path: "/jobs/:jobSlug", component: { template: "<div>Job Overview</div>" } },
    ],
  });
  return router;
}

function mockFetch(opts: {
  projects?: unknown;
  postStatus?: number;
  postBody?: unknown;
}) {
  const projects = opts.projects ?? fakeProjects;
  const postStatus = opts.postStatus ?? 201;
  const postBody = opts.postBody ?? fakeSuccessResponse;

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST" && url.includes("/api/jobs")) {
        return {
          ok: postStatus >= 200 && postStatus < 300,
          status: postStatus,
          statusText: postStatus === 422 ? "Unprocessable Entity" : "Created",
          json: () => Promise.resolve(postBody),
        };
      }
      if (url.includes("/api/projects")) {
        return { ok: true, status: 200, json: () => Promise.resolve(projects) };
      }
      return { ok: false, status: 404, json: () => Promise.resolve({}) };
    }),
  );
}

function mountJobSubmit() {
  const router = makeRouter();
  const pinia = createPinia();
  setActivePinia(pinia);
  return {
    wrapper: mount(JobSubmit, { global: { plugins: [router, pinia] } }),
    router,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("JobSubmit", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // ── Rendering ──────────────────────────────────────────────────────────

  it("renders a form (not a stub)", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    // Should have a real form, not the StubView placeholder
    expect(wrapper.find("form").exists()).toBe(true);
  });

  it("renders a project selector", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    expect(wrapper.find("select").exists()).toBe(true);
  });

  it("populates project selector from /api/projects", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    const options = wrapper.findAll("option");
    const slugs = options.map((o) => o.element.value);
    expect(slugs).toContain("alpha");
    expect(slugs).toContain("beta");
  });

  it("renders job-type radios for build-feature and fix-bug", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    const radios = wrapper
      .findAll("input[type='radio']")
      .map((r) => (r.element as HTMLInputElement).value);
    expect(radios).toContain("build-feature");
    expect(radios).toContain("fix-bug");
  });

  it("renders a title input", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    expect(
      wrapper.find("input[type='text']").exists() || wrapper.find("input:not([type])").exists(),
    ).toBe(true);
  });

  it("renders a request textarea", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    expect(wrapper.find("textarea").exists()).toBe(true);
  });

  it("renders a dry-run toggle", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    expect(wrapper.find("input[type='checkbox']").exists()).toBe(true);
  });

  // ── Slug preview ────────────────────────────────────────────────────────

  it("shows slug preview after typing a title", async () => {
    mockFetch({});
    const { wrapper } = mountJobSubmit();
    await flushPromises();
    const titleInput = wrapper.find("input[type='text']");
    await titleInput.setValue("Fix login crash");
    expect(wrapper.text()).toMatch(/fix-login-crash/);
  });

  // ── Successful submit ───────────────────────────────────────────────────

  it("redirects to job overview after successful submit", async () => {
    mockFetch({ postBody: fakeSuccessResponse });
    const { wrapper, router } = mountJobSubmit();
    await flushPromises();

    await wrapper.find("select").setValue("alpha");
    await wrapper.find("input[value='fix-bug']").setValue("fix-bug");
    await wrapper.find("input[type='text']").setValue("Fix login crash");
    await wrapper.find("textarea").setValue("The form crashes on empty password.");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe(
      `/jobs/${fakeSuccessResponse.job_slug}`,
    );
  });

  // ── Compile errors ──────────────────────────────────────────────────────

  it("shows compile error message on 422 response", async () => {
    mockFetch({ postStatus: 422, postBody: fakeCompileErrors });
    const { wrapper } = mountJobSubmit();
    await flushPromises();

    await wrapper.find("select").setValue("alpha");
    await wrapper.find("input[value='fix-bug']").setValue("fix-bug");
    await wrapper.find("input[type='text']").setValue("Fix login crash");
    await wrapper.find("textarea").setValue("The form crashes on empty password.");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toMatch(/project_not_found|no project|error/i);
  });

  it("does not redirect on compile error", async () => {
    mockFetch({ postStatus: 422, postBody: fakeCompileErrors });
    const { wrapper, router } = mountJobSubmit();
    const pushSpy = vi.spyOn(router, "push");
    await flushPromises();

    await wrapper.find("select").setValue("alpha");
    await wrapper.find("input[value='fix-bug']").setValue("fix-bug");
    await wrapper.find("input[type='text']").setValue("Fix login crash");
    await wrapper.find("textarea").setValue("The form crashes on empty password.");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(pushSpy).not.toHaveBeenCalled();
  });

  // ── Dry-run preview ─────────────────────────────────────────────────────

  it("shows stage list after dry-run response", async () => {
    mockFetch({ postBody: fakeDryRunResponse, postStatus: 201 });
    const { wrapper } = mountJobSubmit();
    await flushPromises();

    await wrapper.find("select").setValue("alpha");
    await wrapper.find("input[value='fix-bug']").setValue("fix-bug");
    await wrapper.find("input[type='text']").setValue("Fix login crash");
    await wrapper.find("textarea").setValue("The form crashes on empty password.");
    // Enable dry-run
    await wrapper.find("input[type='checkbox']").setValue(true);
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("write-bug-report");
  });

  it("does not redirect on dry-run success", async () => {
    mockFetch({ postBody: fakeDryRunResponse, postStatus: 201 });
    const { wrapper, router } = mountJobSubmit();
    const pushSpy = vi.spyOn(router, "push");
    await flushPromises();

    await wrapper.find("select").setValue("alpha");
    await wrapper.find("input[value='fix-bug']").setValue("fix-bug");
    await wrapper.find("input[type='text']").setValue("Fix login crash");
    await wrapper.find("textarea").setValue("The form crashes on empty password.");
    await wrapper.find("input[type='checkbox']").setValue(true);
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(pushSpy).not.toHaveBeenCalledWith(expect.stringContaining("/jobs/"));
  });
});
