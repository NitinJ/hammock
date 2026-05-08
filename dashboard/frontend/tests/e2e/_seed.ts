/**
 * Disk-seed helpers for Playwright e2e suites.
 *
 * Mirror of the v1 layout helpers in shared/v1/paths.py — we write
 * JSON files directly because launching Python from inside Playwright
 * tests is awkward and slow.
 */

import { mkdirSync, writeFileSync, rmSync } from "node:fs";
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
  rmSync(HAMMOCK_ROOT, { recursive: true, force: true });
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

export interface SeededNodeOptions {
  slug: string;
  nodeId: string;
  state?: "pending" | "running" | "succeeded" | "failed" | "skipped";
  attempts?: number;
  lastError?: string | null;
}

export function seedNode(opts: SeededNodeOptions): void {
  const dir = join(jobDir(opts.slug), "nodes", opts.nodeId);
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

/** Lay down a chat.jsonl at the v1 attempt-dir path. Each entry in
 *  ``lines`` is serialised to a separate JSONL line (claude's
 *  stream-json output: type=system|assistant|user|result).
 *  The dashboard's chat endpoint reads this file. */
export function seedChat(
  slug: string,
  nodeId: string,
  attempt: number,
  lines: Record<string, unknown>[],
): void {
  const dir = join(jobDir(slug), "nodes", nodeId, "runs", String(attempt));
  mkdirSync(dir, { recursive: true });
  const text = lines.map((l) => JSON.stringify(l)).join("\n") + "\n";
  writeFileSync(join(dir, "chat.jsonl"), text);
}

export interface SeededPendingHilOptions {
  slug: string;
  nodeId: string;
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
  writeFileSync(
    join(dir, `${opts.nodeId}.json`),
    JSON.stringify(
      {
        node_id: opts.nodeId,
        output_var_names: [opts.outputVarName],
        output_types: { [opts.outputVarName]: opts.outputType },
        presentation: opts.presentationTitle
          ? { title: opts.presentationTitle }
          : {},
        loop_id: null,
        iteration: null,
        created_at: new Date().toISOString(),
      },
      null,
      2,
    ),
  );
}
