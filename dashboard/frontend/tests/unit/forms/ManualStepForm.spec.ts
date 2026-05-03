import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ManualStepForm from "@/components/forms/ManualStepForm.vue";

const manualQuestion = {
  kind: "manual-step" as const,
  instructions: "Deploy to staging.\nVerify health checks pass.",
  extra_fields: null,
};

describe("ManualStepForm", () => {
  it("renders each instruction line", () => {
    const w = mount(ManualStepForm, { props: { question: manualQuestion, submitting: false } });
    expect(w.text()).toContain("Deploy to staging.");
    expect(w.text()).toContain("Verify health checks pass.");
  });

  it("disables textarea when submitting=true", () => {
    const w = mount(ManualStepForm, { props: { question: manualQuestion, submitting: true } });
    expect(w.find("textarea").attributes("disabled")).toBeDefined();
  });

  it("getAnswer returns kind='manual-step' with output", async () => {
    const w = mount(ManualStepForm, { props: { question: manualQuestion, submitting: false } });
    await w.find("textarea").setValue("Deployed. All green.");
    const answer = (w.vm as { getAnswer(): unknown }).getAnswer() as {
      kind: string;
      output: string;
    };
    expect(answer.kind).toBe("manual-step");
    expect(answer.output).toBe("Deployed. All green.");
  });
});
