import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ReviewForm from "@/components/forms/ReviewForm.vue";

const reviewQuestion = {
  kind: "review" as const,
  target: "design-spec.md",
  prompt: "Approve the design spec?",
};

describe("ReviewForm", () => {
  it("renders the review prompt", () => {
    const w = mount(ReviewForm, { props: { question: reviewQuestion, submitting: false } });
    expect(w.text()).toContain("Approve the design spec?");
  });

  it("renders the artifact target", () => {
    const w = mount(ReviewForm, { props: { question: reviewQuestion, submitting: false } });
    expect(w.text()).toContain("design-spec.md");
  });

  it("renders approve and reject buttons", () => {
    const w = mount(ReviewForm, { props: { question: reviewQuestion, submitting: false } });
    expect(w.text()).toContain("Approve");
    expect(w.text()).toContain("Reject");
  });

  it("disables buttons when submitting=true", () => {
    const w = mount(ReviewForm, { props: { question: reviewQuestion, submitting: true } });
    const buttons = w.findAll("button");
    buttons.forEach((b) => expect(b.attributes("disabled")).toBeDefined());
  });

  it("getAnswer returns kind='review' with decision", async () => {
    const w = mount(ReviewForm, { props: { question: reviewQuestion, submitting: false } });
    await w.find(".btn-approve").trigger("click");
    await w.find("textarea").setValue("Looks good");
    const answer = (w.vm as { getAnswer(): unknown }).getAnswer() as {
      kind: string;
      decision: string;
      comments: string;
    };
    expect(answer.kind).toBe("review");
    expect(answer.decision).toBe("approve");
    expect(answer.comments).toBe("Looks good");
  });
});
