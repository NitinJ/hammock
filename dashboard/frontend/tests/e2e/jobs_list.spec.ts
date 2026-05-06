import { expect, test } from "@playwright/test";
import { nuke, seedJob } from "./_seed";

test.beforeEach(() => {
  nuke();
  seedJob({ slug: "alpha-2026-01-01", workflowName: "t-test", state: "running" });
  seedJob({ slug: "beta-2026-01-02", workflowName: "t-other", state: "completed" });
});

test("jobs list renders rows and navigates on click", async ({ page }) => {
  await page.goto("/jobs");
  await expect(page.getByRole("heading", { name: "Jobs" })).toBeVisible();
  await expect(page.getByText("alpha-2026-01-01")).toBeVisible();
  await expect(page.getByText("beta-2026-01-02")).toBeVisible();

  await page.getByText("alpha-2026-01-01").click();
  await expect(page).toHaveURL(/\/jobs\/alpha-2026-01-01$/);
});
