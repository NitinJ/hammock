import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import AskForm from "@/components/forms/AskForm.vue";

const askQuestion = { kind: "ask" as const, text: "Use Argon2id?", options: ["yes", "no"] };
const askQuestionFreeText = { kind: "ask" as const, text: "What is your preference?", options: null };

describe("AskForm", () => {
  it("renders the question text", () => {
    const w = mount(AskForm, { props: { question: askQuestion, submitting: false } });
    expect(w.text()).toContain("Use Argon2id?");
  });

  it("renders radio options when options list is provided", () => {
    const w = mount(AskForm, { props: { question: askQuestion, submitting: false } });
    const inputs = w.findAll('input[type="radio"]');
    expect(inputs).toHaveLength(2);
    expect(w.text()).toContain("yes");
    expect(w.text()).toContain("no");
  });

  it("does not render radio inputs for free-text question", () => {
    const w = mount(AskForm, { props: { question: askQuestionFreeText, submitting: false } });
    const inputs = w.findAll('input[type="radio"]');
    expect(inputs).toHaveLength(0);
  });

  it("disables inputs when submitting=true", () => {
    const w = mount(AskForm, { props: { question: askQuestion, submitting: true } });
    const textarea = w.find("textarea");
    expect(textarea.attributes("disabled")).toBeDefined();
  });

  it("getAnswer returns kind='ask' with text", async () => {
    const w = mount(AskForm, { props: { question: askQuestion, submitting: false } });
    await w.find("textarea").setValue("My answer");
    const answer = (w.vm as { getAnswer(): unknown }).getAnswer() as {
      kind: string;
      text: string;
      choice: string | null;
    };
    expect(answer.kind).toBe("ask");
    expect(answer.text).toBe("My answer");
  });

  it("getAnswer includes selected choice", async () => {
    const w = mount(AskForm, { props: { question: askQuestion, submitting: false } });
    const radios = w.findAll('input[type="radio"]');
    await radios[0].setValue("yes");
    const answer = (w.vm as { getAnswer(): unknown }).getAnswer() as { choice: string | null };
    expect(answer.choice).toBe("yes");
  });
});
