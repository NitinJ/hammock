import { createRouter, createWebHistory } from "vue-router";

import JobsList from "@/views/JobsList.vue";
import JobDetail from "@/views/JobDetail.vue";
import NewJob from "@/views/NewJob.vue";
import OrchestratorView from "@/views/OrchestratorView.vue";

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
  ],
});
