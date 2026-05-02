"""``hammock job ...`` — Stage 3 ships ``submit``; later stages add list/cancel.

``submit`` invokes the Plan Compiler synchronously per design doc § Plan
Compiler § Where the compiler runs. On success: prints the new job slug
+ path. On compile failure: prints structured errors and exits non-zero
without writing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from dashboard.compiler import CompileFailure, CompileSuccess, compile_job

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

job_app = typer.Typer(
    name="job",
    help="Manage hammock jobs.",
    no_args_is_help=True,
)


def _hammock_root() -> Path | None:
    """Read HAMMOCK_ROOT env override; ``None`` falls through to module default."""
    import os

    env = os.environ.get("HAMMOCK_ROOT")
    return Path(env).expanduser().resolve() if env else None


@job_app.command("submit")
def submit(
    project: Annotated[str, typer.Option("--project", help="Project slug.")],
    job_type: Annotated[str, typer.Option("--type", help="Job type, e.g. build-feature.")],
    title: Annotated[
        str,
        typer.Option("--title", help="Short human-readable title; used to derive the job slug."),
    ],
    request_text: Annotated[
        str | None,
        typer.Option(
            "--request-text",
            help="The human's request prompt as a string (mutually exclusive with --request-file).",
        ),
    ] = None,
    request_file: Annotated[
        Path | None,
        typer.Option(
            "--request-file",
            help="Path to a file containing the request prompt.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Validate + return the would-be plan without writing the job dir.",
        ),
    ] = False,
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit JSON output (success or failures).")
    ] = False,
) -> None:
    """Compile a job submission and (unless --dry-run) write the job dir."""
    if request_text is None and request_file is None:
        err_console.print("[red]One of --request-text or --request-file is required.[/red]")
        raise typer.Exit(code=2)
    if request_text is not None and request_file is not None:
        err_console.print("[red]--request-text and --request-file are mutually exclusive.[/red]")
        raise typer.Exit(code=2)

    if request_file is not None:
        try:
            request_text = request_file.read_text()
        except OSError as e:
            err_console.print(f"[red]Could not read --request-file {request_file}: {e}[/red]")
            raise typer.Exit(code=1) from e

    assert request_text is not None  # for type checker

    result = compile_job(
        project_slug=project,
        job_type=job_type,
        title=title,
        request_text=request_text,
        root=_hammock_root(),
        dry_run=dry_run,
    )

    if isinstance(result, CompileSuccess):
        if json_out:
            typer.echo(
                json.dumps(
                    {
                        "ok": True,
                        "job_slug": result.job_slug,
                        "job_dir": str(result.job_dir),
                        "stage_count": len(result.stages),
                        "dry_run": result.dry_run,
                    },
                    indent=2,
                )
            )
            return
        if result.dry_run:
            console.print(
                f"[green]dry-run OK[/green] — would create [bold]{result.job_slug}[/bold] "
                f"with {len(result.stages)} stages"
            )
            console.print(f"  job_dir would be: {result.job_dir}")
        else:
            console.print(
                f"[green]Submitted[/green] [bold]{result.job_slug}[/bold] "
                f"({len(result.stages)} stages)"
            )
            console.print(f"  job_dir: {result.job_dir}")
        return

    # Failure path
    failures: list[CompileFailure] = result
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "ok": False,
                    "failures": [
                        {"kind": f.kind, "stage_id": f.stage_id, "message": f.message}
                        for f in failures
                    ],
                },
                indent=2,
            )
        )
        raise typer.Exit(code=1)

    err_console.print(f"[red bold]Compile failed[/red bold] — {len(failures)} failure(s):")
    table = Table(show_header=True, header_style="bold")
    table.add_column("kind")
    table.add_column("stage_id")
    table.add_column("message")
    for f in failures:
        table.add_row(f.kind, f.stage_id or "—", f.message)
    err_console.print(table)
    raise typer.Exit(code=1)
