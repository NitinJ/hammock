/**
 * Stage D — live chat tail + scroll preservation + nested-loop iter rows.
 *
 * The dashboard's SSE pipeline emits a `file_kind: "chat_jsonl"`
 * PathChange whenever a node's chat.jsonl is modified. The frontend
 * dispatches on `file_kind` and invalidates the matching agentChat
 * cache so the tail re-renders without manual refresh.
 */

import { utimesSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { jobDir, seedChat, seedJob, seedNode } from "./_seed";

function bump(path: string): void {
  // Bump mtime so the dashboard watcher's poll catches the change
  // even if the previous mtime had the same second-resolution.
  const t = new Date();
  utimesSync(path, t, t);
}

test("nested-loop fixture: left pane shows iter rows for both outer iters", async ({ page }) => {
  const slug = "nested-loop-2026-05-08";
  seedJob({
    slug,
    workflowName: "t-nested",
    state: "running",
    workflowYaml: `schema_version: 1
workflow: t-nested
variables:
  spec: { type: design-spec }
nodes:
  - id: outer-loop
    kind: loop
    name: "Outer loop"
    body:
      - id: write-spec
        kind: artifact
        actor: agent
`,
  });
  // Two outer iters, each with the body-node executed.
  seedNode({ slug, nodeId: "write-spec", iterPath: [0], state: "succeeded" });
  seedNode({ slug, nodeId: "write-spec", iterPath: [1], state: "running" });
  // Seed a chat tail for iter 1 so the tail can render when clicked.
  seedChat({
    slug,
    nodeId: "write-spec",
    iterPath: [1],
    attempt: 1,
    lines: [
      { type: "system", subtype: "init", session_id: "iter1abc", cwd: "/repo" },
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "Iter 1 work" }] },
      },
    ],
  });

  await page.goto(`/jobs/${slug}`);

  // Iter rows for both outer iters appear in the left pane.
  await expect(page.getByText("write-spec").nth(0)).toBeVisible();
  await expect(page.getByText("write-spec").nth(1)).toBeVisible();

  // Click iter 1's body row → URL gains `?node=write-spec&iter=1`.
  // The two rows have identical labels, so we drive selection by URL
  // directly; the click test above proves the row is rendered.
  await page.goto(`/jobs/${slug}?node=write-spec&iter=1`);
  const chat = page.getByTestId("agent-chat-tail");
  await expect(chat).toBeVisible();
  await expect(chat.getByTestId("chat-block-text")).toContainText("Iter 1 work");
});

test("live chat update: appended turn shows up without manual refresh", async ({ page }) => {
  const slug = "chat-live-2026-05-08";
  seedJob({
    slug,
    workflowName: "t-live",
    state: "running",
    workflowYaml: `schema_version: 1
workflow: t-live
variables:
  spec: { type: design-spec }
nodes:
  - id: write-spec
    kind: artifact
    actor: agent
`,
  });
  seedNode({ slug, nodeId: "write-spec", state: "running" });
  seedChat({
    slug,
    nodeId: "write-spec",
    attempt: 1,
    lines: [
      { type: "system", subtype: "init", session_id: "live0001", cwd: "/repo" },
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "First turn" }] },
      },
    ],
  });

  await page.goto(`/jobs/${slug}?node=write-spec`);
  const chat = page.getByTestId("agent-chat-tail");
  await expect(chat).toBeVisible();
  await expect(chat).toContainText("First turn");
  await expect(chat).not.toContainText("Second turn");

  // Append a new turn to chat.jsonl + bump mtime so the SSE watcher
  // emits the chat_jsonl event the frontend listens for. Repeated
  // bumps in the polling loop guard against the watcher's coalescing
  // window dropping a single isolated mtime poke.
  seedChat({
    slug,
    nodeId: "write-spec",
    attempt: 1,
    append: true,
    lines: [
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "Second turn" }] },
      },
    ],
  });
  const chatPath = join(jobDir(slug), "nodes", "write-spec", "top", "runs", "1", "chat.jsonl");
  await expect
    .poll(
      async () => {
        bump(chatPath);
        return await chat.textContent();
      },
      { timeout: 15_000, intervals: [500, 1000, 1500] },
    )
    .toContain("Second turn");
});

test("scroll preservation: SSE poke does not yank scroll while user reads earlier turns", async ({
  page,
}) => {
  const slug = "chat-scroll-2026-05-08";
  seedJob({
    slug,
    workflowName: "t-scroll",
    state: "running",
    workflowYaml: `schema_version: 1
workflow: t-scroll
variables:
  spec: { type: design-spec }
nodes:
  - id: write-spec
    kind: artifact
    actor: agent
`,
  });
  seedNode({ slug, nodeId: "write-spec", state: "running" });
  // Enough long turns that the scroller overflows; not so many that
  // markdown re-rendering dominates the SSE round-trip budget.
  const longTurns: Record<string, unknown>[] = [
    { type: "system", subtype: "init", session_id: "scrollx", cwd: "/repo" },
  ];
  for (let i = 0; i < 8; i++) {
    longTurns.push({
      type: "assistant",
      message: {
        role: "assistant",
        content: [
          {
            type: "text",
            text: `## Turn ${i}\n\n${"Lorem ipsum dolor sit amet ".repeat(40)}`,
          },
        ],
      },
    });
  }
  seedChat({
    slug,
    nodeId: "write-spec",
    attempt: 1,
    lines: longTurns,
  });

  await page.goto(`/jobs/${slug}?node=write-spec`);
  const chat = page.getByTestId("agent-chat-tail");
  await expect(chat).toBeVisible();
  // First and last turns both rendered → initial load fully settled.
  await expect(chat).toContainText("Turn 0");
  await expect(chat).toContainText("Turn 7");

  const scroller = chat.locator("> div.overflow-auto");
  // Scroll the scroller to the very top so the user is reading the
  // earliest turns. A real user would scroll up via wheel; we just
  // set scrollTop directly.
  await scroller.evaluate((el) => {
    (el as HTMLElement).scrollTop = 0;
  });
  // Sanity: after the bottom-stick on initial render, we just put
  // the scroll at 0; confirm.
  const before = await scroller.evaluate((el) => (el as HTMLElement).scrollTop);
  expect(before).toBe(0);

  // Append a new turn + bump mtime → SSE chat_jsonl → invalidate +
  // refetch. The component must NOT yank the scroll back to bottom.
  seedChat({
    slug,
    nodeId: "write-spec",
    attempt: 1,
    append: true,
    lines: [
      {
        type: "assistant",
        message: { role: "assistant", content: [{ type: "text", text: "Late arrival" }] },
      },
    ],
  });
  const chatPath = join(jobDir(slug), "nodes", "write-spec", "top", "runs", "1", "chat.jsonl");
  // Poll-then-bump to defeat coalescing if the watcher missed the
  // first append's mtime change.
  await expect
    .poll(
      async () => {
        bump(chatPath);
        return await chat.textContent();
      },
      { timeout: 15_000, intervals: [500, 1000, 1500] },
    )
    .toContain("Late arrival");

  // Scroll position is preserved — still at the top, NOT scrolled
  // down to the new turn.
  const after = await scroller.evaluate((el) => (el as HTMLElement).scrollTop);
  expect(after).toBe(0);
});
