import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { jobDir, nuke, seedJob, seedPendingHil } from "./_seed";

const SLUG = "hil-2026-01-01";

test.beforeEach(() => {
  nuke();
  seedJob({
    slug: SLUG,
    workflowName: "t-hil",
    state: "blocked_on_human",
    workflowYaml: `schema_version: 1
workflow: t-hil
variables:
  spec:   { type: design-spec }
  review: { type: review-verdict }
nodes:
  - id: review-spec-human
    kind: artifact
    actor: human
    inputs:  { spec: $spec }
    outputs: { review: $review }
    presentation: { title: "Review the spec" }
`,
  });
  // Seed the upstream `spec` envelope so the engine's review-verdict
  // produce can resolve inputs (not strictly required for form render,
  // but makes the file shape match what the engine would write).
  const specPath = join(jobDir(SLUG), "variables", "spec__top.json");
  writeFileSync(
    specPath,
    JSON.stringify({
      type: "design-spec",
      type_version: 1,
      repo: null,
      producer_node: "write-spec",
      produced_at: "2026-01-01T00:00:00",
      value: { title: "T", overview: "X", document: "## D\n\n." },
    }),
  );
  seedPendingHil({
    slug: SLUG,
    nodeId: "review-spec-human",
    outputVarName: "review",
    outputType: "review-verdict",
    presentationTitle: "Review the spec",
  });
});

test("HIL queue renders an explicit gate and submits to the right marker path", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });

  await page.goto("/hil");
  await expect(page.getByRole("heading", { name: "Review inbox" })).toBeVisible();
  await expect(page.getByText("Review the spec")).toBeVisible();

  await page.getByRole("button", { name: "approved" }).click();
  // review-verdict has two textareas: summary, then document.
  const textareas = page.getByPlaceholder("Type here…");
  await textareas.nth(0).fill("looks great, ship it");
  await textareas.nth(1).fill("## Review\n\nlooks great, ship it");

  const submission = page.waitForResponse(
    (r) =>
      r.url().includes("/api/hil/") &&
      r.url().endsWith("/answer") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /^Submit$/ }).click();
  const resp = await submission;
  expect(resp.status(), `errors: ${errors.join(" | ")}`).toBe(200);

  // After submission the pending marker is removed by the engine.
  await expect
    .poll(() => existsSync(join(jobDir(SLUG), "pending", "review-spec-human__top.json")))
    .toBe(false);

  // And the variable envelope landed.
  const envPath = join(jobDir(SLUG), "variables", "review__top.json");
  await expect.poll(() => existsSync(envPath)).toBe(true);
  const env = JSON.parse(readFileSync(envPath, "utf-8"));
  expect(env.type).toBe("review-verdict");
  expect(env.value.verdict).toBe("approved");
});
