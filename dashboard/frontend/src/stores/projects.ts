import { defineStore } from "pinia";
import { ref } from "vue";
import type { ProjectListItem } from "@/api/schema.d";

export const useProjectsStore = defineStore("projects", () => {
  const items = ref<ProjectListItem[]>([]);

  function setProjects(list: ProjectListItem[]): void {
    items.value = list;
  }

  function patchProject(slug: string, patch: Partial<ProjectListItem>): void {
    const idx = items.value.findIndex((p) => p.slug === slug);
    if (idx !== -1) {
      items.value[idx] = { ...items.value[idx]!, ...patch };
    }
  }

  return { items, setProjects, patchProject };
});
