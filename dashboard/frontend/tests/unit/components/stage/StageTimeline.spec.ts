import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import type { StageListEntry } from "@/api/schema.d";
import StageTimeline from "@/components/stage/StageTimeline.vue";

const mockStages: StageListEntry[] = [
  {
    stage_id: "design",
    state: "COMPLETED",
    attempt: 1,
    started_at: "2026-05-01T08:00:00Z",
    ended_at: "2026-05-01T08:30:00Z",
    cost_accrued: 0.12,
  },
  {
    stage_id: "implement",
    state: "RUNNING",
    attempt: 1,
    started_at: "2026-05-01T08:31:00Z",
    ended_at: null,
    cost_accrued: 0.35,
  },
  {
    stage_id: "review",
    state: "PENDING",
    attempt: 1,
    started_at: null,
    ended_at: null,
    cost_accrued: 0,
  },
];

describe("StageTimeline", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("renders all stage ids", () => {
    const wrapper = mount(StageTimeline, {
      props: { stages: mockStages, jobSlug: "feat-auth-20260501" },
      global: {
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    expect(wrapper.text()).toContain("design");
    expect(wrapper.text()).toContain("implement");
    expect(wrapper.text()).toContain("review");
  });

  it("renders stage states", () => {
    const wrapper = mount(StageTimeline, {
      props: { stages: mockStages, jobSlug: "feat-auth-20260501" },
      global: {
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    // StateBadge renders COMPLETED as "Completed", RUNNING as "Running"
    expect(wrapper.text()).toContain("Completed");
    expect(wrapper.text()).toContain("Running");
  });

  it("renders cost accrued per stage", () => {
    const wrapper = mount(StageTimeline, {
      props: { stages: mockStages, jobSlug: "feat-auth-20260501" },
      global: {
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    expect(wrapper.text()).toContain("0.12");
    expect(wrapper.text()).toContain("0.35");
  });

  it("renders empty state when no stages", () => {
    const wrapper = mount(StageTimeline, {
      props: { stages: [], jobSlug: "feat-auth-20260501" },
      global: {
        stubs: { RouterLink: { template: "<a><slot /></a>" } },
      },
    });
    expect(wrapper.text()).toContain("No stages");
  });

  it("links each stage to its live view", () => {
    const wrapper = mount(StageTimeline, {
      props: { stages: mockStages, jobSlug: "feat-auth-20260501" },
      global: {
        stubs: { RouterLink: { template: '<a :href="to"><slot /></a>', props: ["to"] } },
      },
    });
    const links = wrapper.findAll("a");
    expect(links.length).toBeGreaterThanOrEqual(3);
  });
});
