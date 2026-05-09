import { createRouter, createWebHistory } from "vue-router";

import JobsList from "@/views/JobsList.vue";
import JobDetail from "@/views/JobDetail.vue";
import NewJob from "@/views/NewJob.vue";
import OrchestratorView from "@/views/OrchestratorView.vue";
import ProjectAdd from "@/views/ProjectAdd.vue";
import ProjectDetail from "@/views/ProjectDetail.vue";
import Projects from "@/views/Projects.vue";
import Workflows from "@/views/Workflows.vue";
import WorkflowDetail from "@/views/WorkflowDetail.vue";
import WorkflowEditor from "@/views/WorkflowEditor.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "jobs", component: JobsList },
    { path: "/new", name: "new-job", component: NewJob },
    { path: "/jobs/:slug", name: "job-detail", component: JobDetail, props: true },
    {
      path: "/jobs/:slug/orchestrator",
      name: "orchestrator",
      component: OrchestratorView,
      props: true,
    },
    { path: "/workflows", name: "workflows", component: Workflows },
    { path: "/workflows/new", name: "workflow-new", component: WorkflowEditor, props: true },
    {
      path: "/workflows/:name",
      name: "workflow-detail",
      component: WorkflowDetail,
      props: true,
    },
    {
      path: "/workflows/:name/edit",
      name: "workflow-edit",
      component: WorkflowEditor,
      props: true,
    },
    { path: "/projects", name: "projects", component: Projects },
    { path: "/projects/new", name: "project-new", component: ProjectAdd },
    {
      path: "/projects/:slug",
      name: "project-detail",
      component: ProjectDetail,
      props: true,
    },
    {
      path: "/projects/:slug/workflows/new",
      name: "project-workflow-new",
      component: WorkflowEditor,
      props: (route) => ({ projectSlug: route.params.slug, name: "" }),
    },
    {
      path: "/projects/:slug/workflows/:name/edit",
      name: "project-workflow-edit",
      component: WorkflowEditor,
      props: (route) => ({
        projectSlug: route.params.slug,
        name: route.params.name,
      }),
    },
  ],
});
