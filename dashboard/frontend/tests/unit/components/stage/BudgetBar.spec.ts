import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import BudgetBar from "@/components/stage/BudgetBar.vue";

describe("BudgetBar", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders cost figures", () => {
    const wrapper = mount(BudgetBar, {
      props: { costUsd: 4.21, budgetUsd: 10.0 },
    });
    expect(wrapper.text()).toContain("4.21");
    expect(wrapper.text()).toContain("10");
  });

  it("renders a progress indicator", () => {
    const wrapper = mount(BudgetBar, {
      props: { costUsd: 5.0, budgetUsd: 10.0 },
    });
    // 50% fill — look for role=progressbar or a width style
    const html = wrapper.html();
    expect(html).toMatch(/50|progressbar|width/i);
  });
});
