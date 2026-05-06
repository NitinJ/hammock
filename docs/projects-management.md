# Projects management

Add a "Projects" surface to Hammock so the operator can register a local
checkout once and re-use it across jobs. Replaces today's flow where the
job-submit form silently fails on code-kind workflows because no project
exists on disk.

This is a feature addition, not part of Phase 2's stage plan. Lives outside
the impl-patch so the latter remains an archive of the v0→v1 cutover.

---

## Goal

After this lands, the operator can:

1. Open the dashboard's **Projects** page.
2. Add a project by pasting the absolute path to a local git checkout.
3. See it listed with its slug, remote URL, default branch, and last
   verify status.
4. Submit a job against it from the New Job form (the project dropdown
   gets populated from the same source of truth).
5. Re-verify or delete a project at any time.

Code-kind workflows submitted against a registered project succeed; the
engine's substrate setup copies the local checkout into
`<job_dir>/repo` per job and operates from that copy.

---

## Why local-path-only (not GitHub URL)

Real projects depend on `.env` files, lockfiles override patches, and
other local state that isn't in git. A clone-from-remote substrate would
miss all of it. Copying the operator's existing checkout is the only
honest option; URL-mode is a footgun that we'd discover the first time
something fails to start because `DATABASE_URL` is unset.

One mode in, one mode out. No fallback.

---

## Substrate model change

Today (engine/v1/driver.submit_job): when a workflow has any code-kind
node, the engine clones from `repo_url` into `<job_dir>/repo`.

After: the engine **copies** from the project's `repo_path` into
`<job_dir>/repo`. Whatever is on disk (tracked, untracked, `.env`,
`.git`) lands in the copy. The engine then runs as before — creates the
job branch (`hammock/jobs/<slug>`) **off the project's `default_branch`,
not off the operator's current HEAD**, allocates worktrees, pushes to
`origin` (preserved by the copy).

### Things to be explicit about

- **Job branch base = `default_branch`, regardless of where the
  operator's HEAD is.** The operator's working branch is irrelevant to
  the job. We `git checkout <default_branch>` inside the copy before
  branching, so feature-branch state doesn't pollute the job.
- **The operator's working tree is read-only to Hammock.** `cp -R`
  reads from `repo_path` and writes only to `<job_dir>/repo/`.
- **Concurrent jobs against the same project** = independent copies,
  no shared state.
- **Cost of copy.** A repo with `node_modules` / `.venv` / `target/`
  could be GBs. Acceptable for first dogfood; an exclude list (e.g.
  `<repo>/.hammockignore`) is a future addition.

---

## `project.json` schema

```json
{
  "slug": "string",
  "name": "string",
  "repo_path": "absolute path to local checkout (required)",
  "remote_url": "git remote get-url origin (captured at register time)",
  "default_branch": "git symbolic-ref refs/remotes/origin/HEAD or fallback main",
  "created_at": "iso8601",
  "last_health_check_at": "iso8601 | null",
  "last_health_check_status": "pass | warn | fail | null"
}
```

Slug is derived from the folder basename, lowercased + hyphenated.
User-overridable on the add form.

---

## Verify operations

Run on register and on `POST /api/projects/{slug}/verify`:

1. `repo_path` exists, is a directory, contains `.git/`.
2. `git -C <repo_path> remote get-url origin` returns a URL → captured
   as `remote_url`.
3. `git -C <repo_path> symbolic-ref refs/remotes/origin/HEAD` → strip
   `refs/remotes/origin/` to get `default_branch`. Fallback chain:
   `main` → `master`.

Status is `pass` if all three succeed, `warn` if (3) had to fall back,
`fail` otherwise. Reason captured alongside.

We do **not** try to reach the remote at register time (no
`gh repo view`, no `git fetch`). Registration must work offline.

---

## Backend endpoints

| Method | Path | Body | Effect |
|---|---|---|---|
| `GET` | `/api/projects` | — | list (already exists) |
| `GET` | `/api/projects/{slug}` | — | detail (already exists; extended with verify fields) |
| `POST` | `/api/projects` | `{path, slug?, name?}` | verify + write `project.json` |
| `DELETE` | `/api/projects/{slug}` | — | remove `<root>/projects/<slug>/` (does not touch jobs already submitted under this slug) |
| `POST` | `/api/projects/{slug}/verify` | — | re-run verify, update last-check fields |

`POST /api/projects` returns the parsed `ProjectDetail` on success or a
structured failure (same shape as compile failures: `{kind, message}`)
on verify error so the form can surface it.

---

## Engine changes

`engine/v1/driver.submit_job`:

- Replace `repo_url: str | None` parameter with `repo_path: Path | None`.
- Replace the "needs `repo_url + repo_slug`" check with "needs
  `repo_path + repo_slug`" for code-kind workflows.
- Replace the call to the existing clone helper with a new
  `copy_local_repo(repo_path, job_slug, root)` that does `cp -R` plus
  `git checkout <default_branch>` plus `git checkout -b
  hammock/jobs/<slug>`.

`engine/v1/substrate.py`:

- New `copy_local_repo()` helper.
- Old `set_up_job_repo()` (the clone-based one) deleted.

`dashboard/compiler/compile.py`:

- Stops passing `repo_url`. Reads `repo_path + default_branch` from
  `project.json` and passes those instead.

---

## Frontend

Sidebar gains a **Projects** entry between **Jobs** and **HIL**.

Pages:

- `/projects` — list view. Rows: slug, name, repo_path, default_branch,
  status badge, ✕ delete. "Add Project" button top-right.
- `/projects/new` — add form. One text input for absolute path,
  optional slug/name overrides, "Verify" button (calls verify
  preflight) and "Add" button.
- `/projects/:slug` — detail. Same fields as list row + "Re-verify" +
  "Delete" actions.

The JobSubmit form's project dropdown re-uses `useProjects()` — no
changes there beyond the dropdown re-populating live via SSE
invalidation.

### Local path picker

Browsers can't return absolute paths from a file picker (security).
Plain text input where the operator pastes the path. Add a "Verify"
button that pings `POST /api/projects` with `dry_run: true` (or a
separate verify endpoint) so the operator gets immediate feedback
before committing.

OS-native folder picker (e.g. via Electron) is a future option if
pasting paths becomes painful. Not building it now.

---

## Out of scope (deferred)

- Excluding heavy directories from the copy (`.hammockignore`).
- GitHub URL mode.
- Health checks beyond `git remote get-url`/`symbolic-ref` (no
  reachability test, no `gh auth status`).
- Multi-remote projects (we use `origin` only).
- Renaming a registered project (delete + re-add).
- Browser-side directory picker.

---

## Definition of done

- Projects sidebar entry exists; clicking lands on the list.
- Adding a project via local path writes `<root>/projects/<slug>/project.json` with the verified fields.
- Deleting a project removes the dir.
- New Job form's project dropdown populates from the new endpoint and submits successfully against a registered project.
- Submitting a code-kind workflow (T6/fix-bug) against a registered project causes the engine to copy `repo_path` into `<job_dir>/repo`, branches `hammock/jobs/<slug>` off `default_branch`, and runs to completion.
- T1–T5 still green (the substrate change shouldn't affect artifact-only workflows).
- T6 e2e re-run green with the new substrate path.
