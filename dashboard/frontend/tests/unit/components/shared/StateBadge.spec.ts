import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import StateBadge from "@/components/shared/StateBadge.vue";
import type { JobState, StageState, TaskState, HilState } from "@/api/schema.d";

describe("StateBadge", () => {
  const jobStates: JobState[] = [
    "SUBMITTED",
    "STAGES_RUNNING",
    "BLOCKED_ON_HUMAN",
    "COMPLETED",
    "ABANDONED",
    "FAILED",
  ];
  const stageStates: StageState[] = [
    "PENDING",
    "READY",
    "RUNNING",
    "PARTIALLY_BLOCKED",
    "BLOCKED_ON_HUMAN",
    "ATTENTION_NEEDED",
    "WRAPPING_UP",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
  ];
  const taskStates: TaskState[] = ["RUNNING", "BLOCKED_ON_HUMAN", "STUCK", "DONE", "FAILED", "CANCELLED"];
  const hilStates: HilState[] = ["AWAITING", "ANSWERED", "CANCELLED"];

  it("renders a badge for each job state", () => {
    for (const state of jobStates) {
      const wrapper = mount(StateBadge, { props: { state } });
      expect(wrapper.text()).toBeTruthy();
      expect(wrapper.classes().length).toBeGreaterThan(0);
    }
  });

  it("renders a badge for each stage state", () => {
    for (const state of stageStates) {
      const wrapper = mount(StateBadge, { props: { state } });
      expect(wrapper.text()).toBeTruthy();
    }
  });

  it("renders a badge for each task state", () => {
    for (const state of taskStates) {
      const wrapper = mount(StateBadge, { props: { state } });
      expect(wrapper.text()).toBeTruthy();
    }
  });

  it("renders a badge for each HIL state", () => {
    for (const state of hilStates) {
      const wrapper = mount(StateBadge, { props: { state } });
      expect(wrapper.text()).toBeTruthy();
    }
  });

  it("applies running colour class for RUNNING state", () => {
    const wrapper = mount(StateBadge, { props: { state: "RUNNING" } });
    const html = wrapper.html();
    // Should have a blue class for running state
    expect(html).toMatch(/blue|running/);
  });

  it("applies attention colour class for ATTENTION_NEEDED state", () => {
    const wrapper = mount(StateBadge, { props: { state: "ATTENTION_NEEDED" } });
    const html = wrapper.html();
    expect(html).toMatch(/amber|yellow|attention/);
  });

  it("applies success colour class for COMPLETED state", () => {
    const wrapper = mount(StateBadge, { props: { state: "COMPLETED" } });
    const html = wrapper.html();
    expect(html).toMatch(/green|success/);
  });

  it("applies error colour class for FAILED state", () => {
    const wrapper = mount(StateBadge, { props: { state: "FAILED" } });
    const html = wrapper.html();
    expect(html).toMatch(/red|error|fail/);
  });
});
