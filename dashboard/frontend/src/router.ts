import { createRouter, createWebHistory } from "vue-router";

// Lazy-loaded per top-level route. v1 surface — node-centric job page,
// jobs list, HIL inbox, settings.
const Home = () => import("@/views/Home.vue");
const JobsList = () => import("@/views/JobsList.vue");
const JobSubmit = () => import("@/views/JobSubmit.vue");
const JobOverview = () => import("@/views/JobOverview.vue");
const HilQueue = () => import("@/views/HilQueue.vue");
const Settings = () => import("@/views/Settings.vue");

export const ROUTES = [
  { path: "/", component: Home, name: "home", meta: { title: "Hammock" } },
  { path: "/jobs", component: JobsList, name: "jobs-list", meta: { title: "Jobs" } },
  { path: "/jobs/new", component: JobSubmit, name: "job-submit", meta: { title: "New Job" } },
  {
    path: "/jobs/:jobSlug",
    component: JobOverview,
    name: "job-overview",
    meta: { title: "Job" },
  },
  { path: "/hil", component: HilQueue, name: "hil-queue", meta: { title: "HIL" } },
  { path: "/settings", component: Settings, name: "settings", meta: { title: "Settings" } },
] as const;

export const router = createRouter({
  history: createWebHistory(),
  routes: [...ROUTES],
});

router.afterEach((to) => {
  const title = to.meta?.title;
  document.title = title ? `${title} — Hammock` : "Hammock";
});
