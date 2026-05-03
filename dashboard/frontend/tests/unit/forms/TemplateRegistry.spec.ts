import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fetchTemplate } from "@/components/forms/TemplateRegistry";
import type { UiTemplate } from "@/components/forms/TemplateRegistry";

const mockTemplate: UiTemplate = {
  name: "ask-default-form",
  description: null,
  hil_kinds: ["ask"],
  instructions: "Please answer the question.",
  fields: { submit_label: "Submit Answer" },
};

describe("fetchTemplate", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: () => Promise.resolve(mockTemplate),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches template from /api/hil/templates/{name}", async () => {
    const result = await fetchTemplate("ask-default-form");
    expect(fetch).toHaveBeenCalledWith("/api/hil/templates/ask-default-form");
    expect(result?.name).toBe("ask-default-form");
    expect(result?.hil_kinds).toEqual(["ask"]);
  });

  it("appends project_slug query param when provided", async () => {
    await fetchTemplate("spec-review-form", "my-project");
    expect(fetch).toHaveBeenCalledWith(
      "/api/hil/templates/spec-review-form?project_slug=my-project",
    );
  });

  it("returns null on 404", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, json: () => Promise.resolve({}) }),
    );
    const result = await fetchTemplate("no-such-template");
    expect(result).toBeNull();
  });

  it("throws on non-404 error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 500, json: () => Promise.resolve({}) }),
    );
    await expect(fetchTemplate("broken")).rejects.toThrow("HTTP 500");
  });
});
