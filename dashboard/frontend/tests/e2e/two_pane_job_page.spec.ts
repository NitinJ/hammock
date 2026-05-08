import { expect, test } from "@playwright/test";
import { nuke, seedChat, seedJob, seedNode } from "./_seed";

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
  // Outputs collapsible exists and is collapsed by default for agent
  // nodes. Open it to see the empty-output panel.
  const outputs = page.getByTestId("outputs-collapsible");
  await expect(outputs).toBeVisible();
  await expect(outputs).toHaveJSProperty("open", false);
  await outputs.locator("summary").click();
  const panel = page.getByTestId("empty-output-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Node completed — no output produced.");
});

test("agent node renders chat tail from seeded chat.jsonl", async ({
  page,
}) => {
  const chatSlug = "twopane-chat-2026-01-01";
  seedJob({
    slug: chatSlug,
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
  seedNode({ slug: chatSlug, nodeId: "write-bug-report", state: "succeeded" });
  seedChat(chatSlug, "write-bug-report", 1, [
    { type: "system", subtype: "init", session_id: "abcd1234", cwd: "/repo" },
    {
      type: "assistant",
      message: {
        role: "assistant",
        content: [
          { type: "text", text: "## Plan\n\nReading the file." },
          { type: "tool_use", name: "Read", input: { file_path: "src/foo.py" } },
        ],
      },
    },
    {
      type: "result",
      is_error: false,
      num_turns: 3,
      total_cost_usd: 0.0042,
    },
  ]);

  await page.goto(`/jobs/${chatSlug}?node=write-bug-report`);

  // Outputs collapsible visible AND collapsed by default.
  const outputs = page.getByTestId("outputs-collapsible");
  await expect(outputs).toBeVisible();
  await expect(outputs).toHaveJSProperty("open", false);

  // Chat tail visible with the seeded turns.
  const chat = page.getByTestId("agent-chat-tail");
  await expect(chat).toBeVisible();
  await expect(chat.getByTestId("chat-block-text")).toContainText("Plan");
  await expect(chat.getByTestId("chat-block-tool-use")).toContainText("Read");
  await expect(chat.getByTestId("chat-block-tool-use")).toContainText("src/foo.py");
  await expect(chat.getByTestId("chat-turn-result")).toContainText("3 turns");
});

test("failed job surfaces a banner with the failed node's last_error", async ({
  page,
}) => {
  const failedSlug = "twopane-failed-2026-01-01";
  seedJob({
    slug: failedSlug,
    workflowName: "t-test",
    state: "failed",
    workflowYaml: `schema_version: 1
workflow: t-test
variables:
  request: { type: job-request }
nodes:
  - id: design-spec-loop
    name: "Design spec — review cycle"
    kind: artifact
    actor: agent
`,
  });
  seedNode({
    slug: failedSlug,
    nodeId: "design-spec-loop",
    state: "failed",
    lastError:
      "loop 'design-spec-loop': predicate never became true after 1 iteration(s)",
  });

  await page.goto(`/jobs/${failedSlug}`);
  const banner = page.getByTestId("job-failure-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Job failed");
  await expect(banner).toContainText("Design spec — review cycle");
  await expect(banner).toContainText("(design-spec-loop)");
  await expect(banner).toContainText("predicate never became true");

  // Click the failed-node button → URL gains ?node=...
  await banner.getByRole("button", { name: /Design spec/ }).click();
  await expect(page).toHaveURL(/node=design-spec-loop/);
});
