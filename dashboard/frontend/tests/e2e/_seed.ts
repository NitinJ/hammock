/**
 * Disk-seed helpers for Playwright e2e suites.
 *
 * Mirror of the v1 layout helpers in shared/v1/paths.py — we write
 * JSON files directly because launching Python from inside Playwright
 * tests is awkward and slow.
 */

import { appendFileSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";

const HAMMOCK_ROOT =
  process.env.HAMMOCK_ROOT ?? "/tmp/hammock-playwright-root";

export function rootPath(...parts: string[]): string {
  return join(HAMMOCK_ROOT, ...parts);
}

export function jobDir(slug: string): string {
  return rootPath("jobs", slug);
}

export function nuke(): void {
  // Clear children of HAMMOCK_ROOT but keep the root directory's inode
  // intact — the dashboard's watchfiles tailer subscribes to the root
  // dir on startup, and removing the root would stop SSE event
  // delivery for subsequent tests in the same session.
  rmSync(rootPath("jobs"), { recursive: true, force: true });
  rmSync(rootPath("projects"), { recursive: true, force: true });
  rmSync(rootPath("fakes"), { recursive: true, force: true });
  mkdirSync(rootPath("jobs"), { recursive: true });
}

export interface SeededJobOptions {
  slug: string;
  workflowName?: string;
  workflowYaml?: string;
  state?:
    | "submitted"
    | "running"
    | "blocked_on_human"
    | "completed"
    | "failed"
    | "cancelled";
  repoSlug?: string | null;
}

/** Lay down a v1 job dir + job.json + workflow.yaml. */
export function seedJob(opts: SeededJobOptions): void {
  const dir = jobDir(opts.slug);
  mkdirSync(join(dir, "variables"), { recursive: true });
  mkdirSync(join(dir, "nodes"), { recursive: true });
  mkdirSync(join(dir, "pending"), { recursive: true });

  const wfYaml =
    opts.workflowYaml ??
    `schema_version: 1\nworkflow: ${opts.workflowName ?? "t-test"}\nvariables: {}\nnodes: []\n`;
  const wfPath = join(dir, "workflow.yaml");
  writeFileSync(wfPath, wfYaml);

  const now = new Date().toISOString();
  const jobConfig = {
    job_slug: opts.slug,
    workflow_name: opts.workflowName ?? "t-test",
    workflow_path: wfPath,
    state: opts.state ?? "submitted",
    repo_slug: opts.repoSlug ?? null,
    submitted_at: now,
    updated_at: now,
  };
  writeFileSync(join(dir, "job.json"), JSON.stringify(jobConfig, null, 2));
}

/** Mirror of `shared/v1/paths.iter_token`: stringify an iter_path tuple
 *  into the directory token used on disk. */
export function iterToken(iter: readonly number[] = []): string {
  if (iter.length === 0) return "top";
  return "i" + iter.join("_");
}

export interface SeededNodeOptions {
  slug: string;
  nodeId: string;
  /** Iteration coordinates; defaults to [] (top-level). */
  iterPath?: readonly number[];
  state?: "pending" | "running" | "succeeded" | "failed" | "skipped";
  attempts?: number;
  lastError?: string | null;
}

export function seedNode(opts: SeededNodeOptions): void {
  const token = iterToken(opts.iterPath ?? []);
  const dir = join(jobDir(opts.slug), "nodes", opts.nodeId, token);
  mkdirSync(dir, { recursive: true });
  const now = new Date().toISOString();
  writeFileSync(
    join(dir, "state.json"),
    JSON.stringify(
      {
        node_id: opts.nodeId,
        state: opts.state ?? "succeeded",
        attempts: opts.attempts ?? 1,
        last_error: opts.lastError ?? null,
        started_at: now,
        finished_at: now,
      },
      null,
      2,
    ),
  );
}

/** Lay down a chat.jsonl at the v2 attempt-dir path
 *  ``nodes/<node_id>/<iter_token>/runs/<attempt>/chat.jsonl``. Each
 *  entry in ``lines`` is serialised to a separate JSONL line (claude's
 *  stream-json output: type=system|assistant|user|result). The
 *  dashboard's chat endpoint reads this file.
 *
 *  Two call shapes (positional kept for back-compat with existing
 *  tests):
 *   - ``seedChat(slug, nodeId, attempt, lines)``       — top-level
 *   - ``seedChat({ slug, nodeId, attempt, lines, iterPath })`` — full
 */
export interface SeededChatOptions {
  slug: string;
  nodeId: string;
  attempt: number;
  iterPath?: readonly number[];
  lines: Record<string, unknown>[];
  /** When true, append the lines to an existing chat.jsonl instead of
   *  overwriting. Used by live-update tests that simulate the agent
   *  emitting another turn while the user has the page open. */
  append?: boolean;
}

export function seedChat(opts: SeededChatOptions): void;
export function seedChat(
  slug: string,
  nodeId: string,
  attempt: number,
  lines: Record<string, unknown>[],
): void;
export function seedChat(
  optsOrSlug: SeededChatOptions | string,
  nodeId?: string,
  attempt?: number,
  lines?: Record<string, unknown>[],
): void {
  const opts: SeededChatOptions =
    typeof optsOrSlug === "string"
      ? {
          slug: optsOrSlug,
          nodeId: nodeId!,
          attempt: attempt!,
          lines: lines!,
        }
      : optsOrSlug;
  const token = iterToken(opts.iterPath ?? []);
  const dir = join(
    jobDir(opts.slug),
    "nodes",
    opts.nodeId,
    token,
    "runs",
    String(opts.attempt),
  );
  mkdirSync(dir, { recursive: true });
  const text = opts.lines.map((l) => JSON.stringify(l)).join("\n") + "\n";
  const path = join(dir, "chat.jsonl");
  if (opts.append) {
    // Append + bump mtime so the watcher sees the change.
    appendFileSync(path, text);
  } else {
    writeFileSync(path, text);
  }
}

export interface SeededPendingHilOptions {
  slug: string;
  nodeId: string;
  iterPath?: readonly number[];
  outputVarName: string;
  /** Variable type — must be a `form_schema()`-producing type so the
   *  form renders. ``review-verdict`` and ``pr-review-verdict`` are
   *  the only ones that satisfy this in v1. */
  outputType: "review-verdict" | "pr-review-verdict";
  presentationTitle?: string;
}

export function seedPendingHil(opts: SeededPendingHilOptions): void {
  const dir = join(jobDir(opts.slug), "pending");
  mkdirSync(dir, { recursive: true });
  const token = iterToken(opts.iterPath ?? []);
  writeFileSync(
    join(dir, `${opts.nodeId}__${token}.json`),
    JSON.stringify(
      {
        node_id: opts.nodeId,
        output_var_names: [opts.outputVarName],
        output_types: { [opts.outputVarName]: opts.outputType },
        presentation: opts.presentationTitle ? { title: opts.presentationTitle } : {},
        iter: opts.iterPath ?? [],
        created_at: new Date().toISOString(),
      },
      null,
      2,
    ),
  );
}
