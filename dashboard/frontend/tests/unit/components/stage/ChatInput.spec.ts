import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import ChatInput from "@/components/stage/ChatInput.vue";

describe("ChatInput", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders a textarea and submit button", () => {
    const wrapper = mount(ChatInput, {
      props: { jobSlug: "job-1", stageId: "implement" },
    });
    expect(wrapper.find("textarea").exists()).toBe(true);
    expect(wrapper.find("button[type='submit']").exists()).toBe(true);
  });

  it("emits send event with text on submit", async () => {
    const wrapper = mount(ChatInput, {
      props: { jobSlug: "job-1", stageId: "implement" },
    });
    await wrapper.find("textarea").setValue("use argon2id");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")).toBeTruthy();
    expect(wrapper.emitted("send")?.[0]).toEqual(["use argon2id"]);
  });

  it("clears textarea after submit", async () => {
    const wrapper = mount(ChatInput, {
      props: { jobSlug: "job-1", stageId: "implement" },
    });
    await wrapper.find("textarea").setValue("some message");
    await wrapper.find("form").trigger("submit");
    expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe("");
  });

  it("does not emit send when textarea is empty", async () => {
    const wrapper = mount(ChatInput, {
      props: { jobSlug: "job-1", stageId: "implement" },
    });
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")).toBeFalsy();
  });
});
