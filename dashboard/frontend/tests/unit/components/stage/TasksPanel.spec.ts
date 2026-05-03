import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import TasksPanel from "@/components/stage/TasksPanel.vue";
import type { TaskRecord } from "@/api/schema.d";

const tasks: TaskRecord[] = [
  { task_id: "task-1", stage_id: "implement", state: "RUNNING", created_at: "2026-05-01T12:00:00Z" },
  { task_id: "task-2", stage_id: "implement", state: "DONE", created_at: "2026-05-01T12:05:00Z" },
];

describe("TasksPanel", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders all task ids", () => {
    const wrapper = mount(TasksPanel, { props: { tasks } });
    expect(wrapper.text()).toContain("task-1");
    expect(wrapper.text()).toContain("task-2");
  });

  it("renders task states", () => {
    const wrapper = mount(TasksPanel, { props: { tasks } });
    expect(wrapper.text()).toMatch(/Running|RUNNING/i);
    expect(wrapper.text()).toMatch(/Done|DONE/i);
  });

  it("shows empty state with no tasks", () => {
    const wrapper = mount(TasksPanel, { props: { tasks: [] } });
    expect(wrapper.text()).toMatch(/no tasks|empty/i);
  });
});
