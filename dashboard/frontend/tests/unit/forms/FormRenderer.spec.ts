import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import FormRenderer from "@/components/forms/FormRenderer.vue";
import type { UiTemplate } from "@/components/forms/TemplateRegistry";

function makeAskDetail() {
  return {
    item: {
      id: "hil-1",
      kind: "ask",
      status: "awaiting",
      question: { kind: "ask", text: "Use Argon2id?", options: ["yes", "no"] },
    },
    job_slug: "job-1",
    project_slug: "proj-1",
    ui_template_name: "ask-default-form",
  };
}

function makeReviewDetail() {
  return {
    item: {
      id: "hil-2",
      kind: "review",
      status: "awaiting",
      question: { kind: "review", target: "design-spec.md", prompt: "Approve?" },
    },
    job_slug: "job-1",
    project_slug: "proj-1",
    ui_template_name: "spec-review-form",
  };
}

function makeManualDetail() {
  return {
    item: {
      id: "hil-3",
      kind: "manual-step",
      status: "awaiting",
      question: { kind: "manual-step", instructions: "Deploy to staging.", extra_fields: null },
    },
    job_slug: "job-1",
    project_slug: "proj-1",
    ui_template_name: "manual-step-default-form",
  };
}

const mockTemplate: UiTemplate = {
  name: "ask-default-form",
  description: null,
  hil_kinds: ["ask"],
  instructions: "Please answer the question.",
  fields: { submit_label: "Submit Answer" },
};

describe("FormRenderer", () => {
  it("renders AskForm for kind=ask", () => {
    const w = mount(FormRenderer, {
      props: { item: makeAskDetail(), template: mockTemplate, submitting: false, error: null },
    });
    expect(w.find(".ask-form").exists()).toBe(true);
    expect(w.find(".review-form").exists()).toBe(false);
  });

  it("renders ReviewForm for kind=review", () => {
    const w = mount(FormRenderer, {
      props: {
        item: makeReviewDetail(),
        template: null,
        submitting: false,
        error: null,
      },
    });
    expect(w.find(".review-form").exists()).toBe(true);
  });

  it("renders ManualStepForm for kind=manual-step", () => {
    const w = mount(FormRenderer, {
      props: {
        item: makeManualDetail(),
        template: null,
        submitting: false,
        error: null,
      },
    });
    expect(w.find(".manual-step-form").exists()).toBe(true);
  });

  it("shows template instructions when template is provided", () => {
    const w = mount(FormRenderer, {
      props: { item: makeAskDetail(), template: mockTemplate, submitting: false, error: null },
    });
    expect(w.text()).toContain("Please answer the question.");
  });

  it("uses template submit_label when set", () => {
    const w = mount(FormRenderer, {
      props: { item: makeAskDetail(), template: mockTemplate, submitting: false, error: null },
    });
    const btn = w.find(".btn-submit");
    expect(btn.text()).toBe("Submit Answer");
  });

  it("uses 'Submit' as default submit label when template has no fields", () => {
    const templateNoFields: UiTemplate = {
      name: "ask-default-form",
      description: null,
      hil_kinds: ["ask"],
      instructions: null,
      fields: null,
    };
    const w = mount(FormRenderer, {
      props: {
        item: makeAskDetail(),
        template: templateNoFields,
        submitting: false,
        error: null,
      },
    });
    expect(w.find(".btn-submit").text()).toBe("Submit");
  });

  it("disables submit button when submitting=true", () => {
    const w = mount(FormRenderer, {
      props: { item: makeAskDetail(), template: null, submitting: true, error: null },
    });
    expect(w.find(".btn-submit").attributes("disabled")).toBeDefined();
  });

  it("shows error message when error prop is set", () => {
    const w = mount(FormRenderer, {
      props: {
        item: makeAskDetail(),
        template: null,
        submitting: false,
        error: "Something went wrong",
      },
    });
    expect(w.text()).toContain("Something went wrong");
  });

  it("emits submit event with answer payload on button click", async () => {
    const w = mount(FormRenderer, {
      props: { item: makeAskDetail(), template: null, submitting: false, error: null },
    });
    await w.find("textarea").setValue("My answer text");
    await w.find(".btn-submit").trigger("click");
    const emitted = w.emitted("submit");
    expect(emitted).toBeTruthy();
    expect(emitted![0][0]).toMatchObject({ kind: "ask", text: "My answer text" });
  });
});
