"""Hammock CLI entry point — ``python -m cli`` / ``hammock`` console script."""

from __future__ import annotations

import typer

from cli.project import project_app

app = typer.Typer(
    name="hammock",
    help="Hammock — agentic development harness CLI.",
    no_args_is_help=True,
)

app.add_typer(project_app, name="project", help="Manage hammock-registered projects.")


if __name__ == "__main__":
    app()
