import { createRouter, createWebHistory } from "vue-router";

// Lazy-loaded per top-level route (locked per design doc § URL topology)
const Home = () => import("@/views/Home.vue");
const ProjectList = () => import("@/views/ProjectList.vue");
const ProjectDetail = () => import("@/views/ProjectDetail.vue");
const JobSubmit = () => import("@/views/JobSubmit.vue");
const JobOverview = () => import("@/views/JobOverview.vue");
const StageLive = () => import("@/views/StageLive.vue");
const ArtifactViewer = () => import("@/views/ArtifactViewer.vue");
const HilQueue = () => import("@/views/HilQueue.vue");
const HilItem = () => import("@/views/HilItem.vue");
const CostDashboard = () => import("@/views/CostDashboard.vue");
const Settings = () => import("@/views/Settings.vue");

export const ROUTES = [
  { path: "/", component: Home, name: "home", meta: { title: "Dashboard Home" } },
  { path: "/projects", component: ProjectList, name: "project-list", meta: { title: "Projects" } },
  {
    path: "/projects/:slug",
    component: ProjectDetail,
    name: "project-detail",
    meta: { title: "Project Detail" },
  },
  { path: "/jobs/new", component: JobSubmit, name: "job-submit", meta: { title: "New Job" } },
  {
    path: "/jobs/:jobSlug",
    component: JobOverview,
    name: "job-overview",
    meta: { title: "Job Overview" },
  },
  {
    path: "/jobs/:jobSlug/stages/:stageId",
    component: StageLive,
    name: "stage-live",
    meta: { title: "Stage Live View" },
  },
  {
    path: "/jobs/:jobSlug/artifacts/:path(.*)*",
    component: ArtifactViewer,
    name: "artifact-viewer",
    meta: { title: "Artifact Viewer" },
  },
  { path: "/hil", component: HilQueue, name: "hil-queue", meta: { title: "HIL Queue" } },
  {
    path: "/hil/:itemId",
    component: HilItem,
    name: "hil-item",
    meta: { title: "HIL Item" },
  },
  {
    path: "/costs",
    component: CostDashboard,
    name: "cost-dashboard",
    meta: { title: "Cost Dashboard" },
  },
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
