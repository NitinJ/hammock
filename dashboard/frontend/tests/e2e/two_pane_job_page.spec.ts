import { expect, test } from "@playwright/test";
import { nuke, seedJob, seedNode } from "./_seed";

const SLUG = "twopane-2026-01-01";

test.beforeEach(() => {
  nuke();
  seedJob({
    slug: SLUG,
    workflowName: "t-test",
    state: "running",
    workflowYaml: `schema_version: 1
workflow: t-test
variables:
  request: { type: job-request }
nodes:
  - id: write-bug-report
    kind: artifact
    actor: agent
`,
  });
  seedNode({ slug: SLUG, nodeId: "write-bug-report", state: "succeeded" });
});

test("two-pane page lists nodes and navigates to detail on click", async ({
  page,
}) => {
  await page.goto(`/jobs/${SLUG}`);

  // Left pane: node list shows the seeded node.
  await expect(page.getByText("write-bug-report").first()).toBeVisible();

  // Right pane: default mode shows the live stream pane header.
  await expect(page.getByText("Live stream")).toBeVisible();

  // Click → URL gains ?node=...
  await page.getByText("write-bug-report").first().click();
  await expect(page).toHaveURL(/node=write-bug-report/);
});

test("succeeded node with no outputs renders the empty-output panel", async ({
  page,
}) => {
  // The seeded node above is succeeded but has no output envelopes —
  // the right pane should show the dogfood-fixes-2 panel: "Node
  // completed — no output produced." rather than the in-progress
  // "no outputs produced yet" placeholder.
  await page.goto(`/jobs/${SLUG}?node=write-bug-report`);
  const panel = page.getByTestId("empty-output-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Node completed — no output produced.");
});
