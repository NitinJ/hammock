import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CostBar from "@/components/shared/CostBar.vue";

describe("CostBar", () => {
  it("renders with no budget cap (no cap display)", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 2.5, budgetCapUsd: null } });
    expect(wrapper.html()).toContain("2.5");
    // No cap ratio shown
    expect(wrapper.html()).not.toMatch(/\d+%/);
  });

  it("renders the cost value", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 4.21, budgetCapUsd: 10 } });
    expect(wrapper.html()).toContain("4.21");
  });

  it("shows ok colour when cost is below 80% of cap", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 5.0, budgetCapUsd: 10 } });
    // 50% — should be in OK zone (green)
    const html = wrapper.html();
    expect(html).toMatch(/green|ok|cost-ok/);
  });

  it("shows warn colour when cost is between 80-100% of cap", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 8.5, budgetCapUsd: 10 } });
    // 85% — warn zone
    const html = wrapper.html();
    expect(html).toMatch(/amber|yellow|warn/);
  });

  it("shows over-budget colour when cost exceeds cap", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 12.0, budgetCapUsd: 10 } });
    // 120% — over budget
    const html = wrapper.html();
    expect(html).toMatch(/red|over|exceed/);
  });

  it("clamps bar width to 100% when over budget", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 15.0, budgetCapUsd: 10 } });
    // Bar should not exceed 100% width
    expect(wrapper.html()).not.toMatch(/width:\s*1[5-9]\d%|width:\s*[2-9]\d{2}%/);
  });

  it("renders percentage when budget cap is present", () => {
    const wrapper = mount(CostBar, { props: { costUsd: 4.0, budgetCapUsd: 10 } });
    expect(wrapper.html()).toMatch(/40%|40 %/);
  });
});
