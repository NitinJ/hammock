import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import FormRenderer from "@/components/hil/FormRenderer.vue";

describe("FormRenderer", () => {
  it("renders Select widget for select:opt1,opt2 fields", () => {
    const wrapper = mount(FormRenderer, {
      props: {
        typeName: "pr-review-verdict",
        fields: [["verdict", "select:merged,needs-revision"]],
        onSubmit: vi.fn(),
      },
    });
    const buttons = wrapper.findAll("button[type='button']");
    expect(buttons.map((b) => b.text())).toEqual(["merged", "needs-revision"]);
  });

  it("renders Textarea widget for textarea field", () => {
    const wrapper = mount(FormRenderer, {
      props: {
        typeName: "review-verdict",
        fields: [["summary", "textarea"]],
        onSubmit: vi.fn(),
      },
    });
    expect(wrapper.find("textarea").exists()).toBe(true);
  });

  it("disables submit until every field has a value", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const wrapper = mount(FormRenderer, {
      props: {
        typeName: "review-verdict",
        fields: [
          ["verdict", "select:approved,needs-revision"],
          ["summary", "textarea"],
        ],
        onSubmit,
      },
    });
    const submitBtn = wrapper.find("button[type='submit']");
    expect(submitBtn.attributes("disabled")).toBeDefined();

    // Pick verdict.
    await wrapper.findAll("button[type='button']")[0]!.trigger("click");
    expect(submitBtn.attributes("disabled")).toBeDefined(); // summary still empty

    // Fill summary.
    const textarea = wrapper.find("textarea");
    await textarea.setValue("looks good");
    expect(submitBtn.attributes("disabled")).toBeUndefined();
  });

  it("calls onSubmit with collected payload when submitted", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const wrapper = mount(FormRenderer, {
      props: {
        typeName: "pr-review-verdict",
        fields: [["verdict", "select:merged,needs-revision"]],
        onSubmit,
      },
    });
    await wrapper.findAll("button[type='button']")[0]!.trigger("click");
    await wrapper.find("form").trigger("submit");
    await nextTick();
    expect(onSubmit).toHaveBeenCalledWith({ verdict: "merged" });
  });

  it("renders the empty-schema message when fields=[]", () => {
    const wrapper = mount(FormRenderer, {
      props: {
        typeName: "bug-report",
        fields: [],
        onSubmit: vi.fn(),
      },
    });
    expect(wrapper.text()).toContain("has no");
    expect(wrapper.text()).toContain("form schema");
  });
});
