"""``hammock project ...`` — seven verbs per design doc § Project Registry.

Identity model:
- ``slug`` — kebab-case, immutable post-registration. Path-derived from the
  basename of the repo path. Slug is the primary key.
- ``name`` — human-readable display string. Mutable via ``rename``. UI-only.

The seven verbs:
- ``register <path>`` — run the init checklist
- ``list`` — one row per project
- ``show <slug>`` — pretty-print project.json + last doctor result
- ``doctor <slug>`` — run full doctor with auto-fix on confirmation
- ``relocate <slug> <new-path>`` — update repo_path (verifies same remote)
- ``rename <slug> <new-name>`` — update display name only
- ``deregister <slug>`` — hard delete with consent + preview

All verbs accept ``--json`` for scripted use. ``register``/``doctor``/
``deregister`` accept ``--yes`` to skip prompts (``register`` skips slug
prompts, ``deregister`` skips the consent prompt). Interactive defaults
mean a human running these by hand sees prompts when needed.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cli import _external
from cli import doctor as _doctor
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.slug import (
    SlugDerivationError,
    derive_slug,
    is_valid_slug,
)

# ``highlight=False`` keeps Rich's ANSI markup we explicitly request (e.g.
# ``[red]...[/red]``) while disabling automatic colorisation of paths /
# numbers, which interpolates ANSI codes into substrings that tests want to
# match against ("myrepo-2026" otherwise renders with the trailing digits
# split into a separate color span).
console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


project_app = typer.Typer(
    name="project",
    help="Manage hammock-registered projects.",
    no_args_is_help=True,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _hammock_root() -> Path:
    """Resolve the hammock root for this CLI invocation.

    Re-read each call so test fixtures setting ``HAMMOCK_ROOT`` take effect
    even though ``shared.paths.HAMMOCK_ROOT`` was captured at import time.
    """
    import os

    env = os.environ.get("HAMMOCK_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return paths.HAMMOCK_ROOT


def _load_project(slug: str, root: Path) -> ProjectConfig:
    """Load and return ProjectConfig; exit 1 with a clear message if not found."""
    p = paths.project_json(slug, root=root)
    if not p.exists():
        err_console.print(f"[red]No project registered with slug {slug!r}[/red]")
        raise typer.Exit(code=1)
    return ProjectConfig.model_validate_json(p.read_text())


def _list_projects(root: Path) -> list[ProjectConfig]:
    base = paths.projects_dir(root)
    if not base.is_dir():
        return []
    out: list[ProjectConfig] = []
    for d in sorted(base.iterdir()):
        cfg = d / "project.json"
        if cfg.exists():
            try:
                out.append(ProjectConfig.model_validate_json(cfg.read_text()))
            except Exception as e:
                err_console.print(f"[yellow]warn:[/yellow] could not load {cfg}: {e}")
    return out


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


@project_app.command("register")
def register(
    path: Annotated[Path, typer.Argument(help="Absolute path to the project repo.")],
    slug: Annotated[
        str | None,
        typer.Option(help="Override the derived slug. Must match [a-z0-9-]+, max 32."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(help="Override the display name (defaults to the slug)."),
    ] = None,
    default_branch: Annotated[
        str | None,
        typer.Option(help="Override default-branch detection."),
    ] = None,
    skip_remote_checks: Annotated[
        bool,
        typer.Option(help="Skip gh auth + reachability checks (offline use)."),
    ] = False,
) -> None:
    """Register *path* as a hammock project."""
    path = path.expanduser().resolve()
    root = _hammock_root()

    # 1. preflight
    if not path.exists() or not path.is_dir():
        err_console.print(f"[red]Path does not exist or is not a directory:[/red] {path}")
        raise typer.Exit(code=1)
    if not _external.git_is_repo(path):
        err_console.print(f"[red]{path} is not a git repository[/red]")
        raise typer.Exit(code=1)

    # 2. already-registered check
    for existing in _list_projects(root):
        if Path(existing.repo_path) == path:
            console.print(
                f"[yellow]Already registered as [bold]{existing.slug}[/bold]; "
                f"use 'hammock project doctor' or 'relocate'.[/yellow]"
            )
            raise typer.Exit(code=0)

    remote_url = _external.git_remote_url(path)
    if remote_url is None:
        err_console.print(f"[red]No 'origin' remote configured at {path}[/red]")
        raise typer.Exit(code=1)

    if not skip_remote_checks:
        if not _external.gh_auth_ok():
            err_console.print("[red]gh auth status failed — run 'gh auth login' first[/red]")
            raise typer.Exit(code=1)
        if not _external.gh_repo_view(remote_url):
            err_console.print(f"[red]Remote not reachable: {remote_url}[/red]")
            raise typer.Exit(code=1)

    if _external.git_working_tree_dirty(path):
        console.print("[yellow]Working tree is dirty; proceeding.[/yellow]")
    if not (path / "CLAUDE.md").exists():
        console.print("[yellow]No CLAUDE.md at repo root; proceeding.[/yellow]")

    # 3. detect default branch
    detected_branch = default_branch or _external.git_default_branch(path)
    if detected_branch is None:
        err_console.print("[red]Could not detect default branch; pass --default-branch[/red]")
        raise typer.Exit(code=1)

    # 4. slug
    if slug is None:
        try:
            derived = derive_slug(path)
        except SlugDerivationError as e:
            err_console.print(f"[red]{e}[/red]\nPass --slug to set one explicitly.")
            raise typer.Exit(code=1) from e
        slug_value = derived
    else:
        if not is_valid_slug(slug):
            err_console.print(f"[red]Invalid slug {slug!r} — must match [a-z0-9-]+ ≤32 chars[/red]")
            raise typer.Exit(code=1)
        slug_value = slug

    # 5. collision
    if (paths.project_dir(slug_value, root=root) / "project.json").exists():
        err_console.print(
            f"[red]Slug {slug_value!r} already taken. Pass --slug to set an alternate.[/red]"
        )
        raise typer.Exit(code=1)

    # 6. write
    project = ProjectConfig(
        slug=slug_value,
        name=name or slug_value,
        repo_path=str(path),
        remote_url=remote_url,
        default_branch=detected_branch,
        created_at=datetime.now(UTC),
    )

    project_dir = paths.project_dir(slug_value, root=root)
    project_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths.project_json(slug_value, root=root), project)

    # symlink hammock_root/projects/<slug>/project_repo → <path>
    symlink = project_dir / "project_repo"
    if symlink.exists() or symlink.is_symlink():
        symlink.unlink()
    symlink.symlink_to(path)

    # override skeleton — empty dirs
    overrides_root = paths.project_overrides_root(path)
    for sub in (
        "agent-overrides",
        "skill-overrides",
        "hook-overrides/quality",
        "job-template-overrides",
        "observatory",
    ):
        (overrides_root / sub).mkdir(parents=True, exist_ok=True)

    # gitignore append
    gi = path / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    if not any(line.strip() in {".hammock/", ".hammock"} for line in existing.splitlines()):
        sep = "" if existing.endswith("\n") or existing == "" else "\n"
        gi.write_text(existing + sep + ".hammock/\n")

    # README in .hammock/
    readme = overrides_root / "README.md"
    if not readme.exists():
        readme.write_text(_OVERRIDE_README)

    # 7. initial doctor (with auto-fix; doctor itself is fast on a fresh register)
    report = _doctor.run_full(project, auto_fix=True, root=root)
    _doctor.write_back(report, project, root=root)

    console.print(
        f"[green]Registered[/green] [bold]{slug_value}[/bold] → {path}\n"
        f"  default branch: {detected_branch}\n"
        f"  remote:         {remote_url}\n"
        f"  doctor:         {report.status}"
    )


_OVERRIDE_README = """\
# .hammock/

This directory holds per-project overrides for hammock — agent definitions,
skill content, hook scripts, job-template tunings, and the per-project
observatory archive.

It is gitignored. Anything here lives only on this checkout. Hammock manages
the layout; you tune the contents.

See: https://github.com/NitinJ/hammock — design.md § Project Registry.
"""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@project_app.command("list")
def list_projects(
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of a table.")] = False,
) -> None:
    """List all registered projects."""
    root = _hammock_root()
    projects = _list_projects(root)

    if json_out:
        # Use stdlib json so ``--json`` output is parseable (no highlight,
        # no Rich wrapping). ``default=str`` handles datetime + Path.
        typer.echo(
            json.dumps(
                [p.model_dump(mode="json") for p in projects],
                indent=2,
                default=str,
            )
        )
        return

    if not projects:
        console.print("(no projects registered)")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("slug")
    table.add_column("name")
    table.add_column("repo path")
    table.add_column("branch")
    table.add_column("health")
    for p in projects:
        health = p.last_health_check_status or "—"
        table.add_row(p.slug, p.name, str(p.repo_path), p.default_branch, health)
    console.print(table)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


@project_app.command("show")
def show(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of pretty.")] = False,
) -> None:
    """Show a single project's metadata."""
    root = _hammock_root()
    project = _load_project(slug, root)

    if json_out:
        typer.echo(json.dumps(project.model_dump(mode="json"), indent=2, default=str))
        return

    console.print(f"[bold]{project.slug}[/bold]   [dim]({project.name})[/dim]")
    console.print(f"  repo path:      {project.repo_path}")
    console.print(f"  remote:         {project.remote_url}")
    console.print(f"  default branch: {project.default_branch}")
    console.print(f"  created at:     {project.created_at.isoformat()}")
    if project.last_health_check_at:
        console.print(
            f"  last doctor:    {project.last_health_check_status} at "
            f"{project.last_health_check_at.isoformat()}"
        )
    else:
        console.print("  last doctor:    (never)")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@project_app.command("doctor")
def doctor_cmd(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Apply auto-fixes without confirm.")
    ] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Emit JSON instead of pretty.")] = False,
) -> None:
    """Run the full health check for *slug*."""
    root = _hammock_root()
    project = _load_project(slug, root)

    report = _doctor.run_full(project, auto_fix=yes, root=root)
    _doctor.write_back(report, project, root=root)

    if json_out:
        payload = {
            "slug": report.slug,
            "tier": report.tier,
            "status": report.status,
            "ran_at": report.ran_at.isoformat(),
            "checks": [
                {
                    "number": c.number,
                    "severity": c.severity,
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "auto_fixed": c.auto_fixed,
                }
                for c in report.checks
            ],
        }
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    console.print(f"[bold]doctor:[/bold] {project.slug} → [bold]{report.status}[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("severity")
    table.add_column("check")
    table.add_column("result")
    table.add_column("message")
    for c in report.checks:
        marker = "✓" if c.passed else "✗"
        sev_color = {"fail": "red", "warn": "yellow", "info": "cyan"}[c.severity]
        table.add_row(
            str(c.number),
            f"[{sev_color}]{c.severity}[/{sev_color}]",
            c.name,
            f"[green]{marker}[/green]" if c.passed else f"[red]{marker}[/red]",
            c.message,
        )
    console.print(table)
    if report.status == "fail":
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# relocate
# ---------------------------------------------------------------------------


@project_app.command("relocate")
def relocate(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    new_path: Annotated[Path, typer.Argument(help="New absolute path of the repo.")],
    force: Annotated[
        bool,
        typer.Option("--force", help="Skip the same-remote-url verification."),
    ] = False,
) -> None:
    """Update *slug*'s ``repo_path`` to *new_path*. Verifies the remote matches."""
    root = _hammock_root()
    project = _load_project(slug, root)

    new_path = new_path.expanduser().resolve()
    if not new_path.exists() or not new_path.is_dir():
        err_console.print(f"[red]New path does not exist or is not a directory:[/red] {new_path}")
        raise typer.Exit(code=1)
    if not _external.git_is_repo(new_path):
        err_console.print(f"[red]{new_path} is not a git repository[/red]")
        raise typer.Exit(code=1)

    if not force:
        new_remote = _external.git_remote_url(new_path)
        if new_remote != project.remote_url:
            err_console.print(
                f"[red]Remote mismatch:[/red] stored={project.remote_url!r} "
                f"new={new_remote!r}\nUse --force to override."
            )
            raise typer.Exit(code=1)

    updated = project.model_copy(update={"repo_path": str(new_path)})
    atomic_write_json(paths.project_json(slug, root=root), updated)

    # Refresh the symlink.
    symlink = paths.project_dir(slug, root=root) / "project_repo"
    if symlink.exists() or symlink.is_symlink():
        symlink.unlink()
    symlink.symlink_to(new_path)

    console.print(f"[green]Relocated[/green] {slug} → {new_path}")


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


@project_app.command("rename")
def rename(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    new_name: Annotated[str, typer.Argument(help="New display name.")],
) -> None:
    """Update *slug*'s display ``name``. Slug is immutable."""
    if not new_name.strip():
        err_console.print("[red]New name must be non-empty[/red]")
        raise typer.Exit(code=1)
    root = _hammock_root()
    project = _load_project(slug, root)
    updated = project.model_copy(update={"name": new_name})
    atomic_write_json(paths.project_json(slug, root=root), updated)
    console.print(f"[green]Renamed[/green] {slug}: {project.name!r} → {new_name!r}")


# ---------------------------------------------------------------------------
# deregister
# ---------------------------------------------------------------------------


@project_app.command("deregister")
def deregister(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the consent prompt.")] = False,
    keep_overrides: Annotated[
        bool,
        typer.Option(
            "--keep-overrides",
            help="Keep <repo_path>/.hammock/ on disk after deregister.",
        ),
    ] = False,
) -> None:
    """Hard delete a project from the registry."""
    root = _hammock_root()
    project = _load_project(slug, root)

    repo = Path(project.repo_path)
    overrides = paths.project_overrides_root(repo)

    # Preview
    console.print(f"[bold]Deregister[/bold] [bold]{slug}[/bold] ({project.name})")
    console.print(f"  repo path:           {repo}")
    console.print("  in-flight jobs:      0   [dim](Stage 4 will detect/cancel)[/dim]")
    console.print("  worktrees:           0   [dim](Stage 4 will clean)[/dim]")
    if not keep_overrides and overrides.exists():
        console.print(f"  remove overrides:    [yellow]rm -rf {overrides}[/yellow]")
    elif keep_overrides:
        console.print("  remove overrides:    [dim]skipped (--keep-overrides)[/dim]")
    console.print(f"  remove registry:     rm -rf {paths.project_dir(slug, root=root)}")
    console.print("\n[red bold]This is irreversible.[/red bold]")

    if not yes:
        confirm = typer.confirm("Continue?", default=False)
        if not confirm:
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(code=1)

    # Perform cleanup
    if not keep_overrides and overrides.exists():
        shutil.rmtree(overrides)

    project_dir = paths.project_dir(slug, root=root)
    if project_dir.exists():
        # Drop the symlink first so we don't accidentally walk into the repo
        symlink = project_dir / "project_repo"
        if symlink.is_symlink():
            symlink.unlink()
        shutil.rmtree(project_dir)

    # Skill symlinks (~/.claude/skills/<slug>__*) — best-effort
    skills_root = Path.home() / ".claude" / "skills"
    if skills_root.is_dir():
        prefix = f"{slug}__"
        for entry in skills_root.iterdir():
            if entry.name.startswith(prefix) and entry.is_symlink():
                entry.unlink()

    console.print(f"[green]Deregistered[/green] {slug}")
