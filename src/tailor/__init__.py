"""The tailor command: ruff format, then house style."""

import os
import sys
import subprocess
from pathlib import Path
import importlib.metadata
from typing import Annotated

import typer

from tailor.config import find_version_problems
from tailor.formatter import Mode, format_files, list_python_files


app = typer.Typer(add_completion = False)


def show_version(value: bool) -> None:
    if value:
        typer.echo(f"tailor {importlib.metadata.version('pytailor')}")
        raise typer.Exit()


@app.command(help = "ruff format, then house style.")
def format_command(
    *,
    paths: Annotated[
        list[Path],
        typer.Argument(
            exists = True,
            show_default = False,
            help = "Files and folders to format.",
        ),
    ],
    line_length: Annotated[
        int | None,
        typer.Option(
            min = 1,
            show_default = False,
            help = "Replaces line-length from \\[tool.tailor] or \\[tool.ruff] in pyproject.toml.",
        ),
    ] = None,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help = "List the files that would change, write nothing.",
        ),
    ] = False,
    diff: Annotated[
        bool,
        typer.Option("--diff", help = "Show the changes as a diff, write nothing."),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback = show_version,
            is_eager = True,
            help = "Show the version and exit.",
        ),
    ] = False,
) -> None:
    if check and diff:
        raise typer.BadParameter(
            "cannot be used together with --diff",
            param_hint = "--check",
        )

    mode = Mode.DIFF if diff else Mode.CHECK if check else Mode.WRITE
    try:
        files = list_python_files(paths = paths)
    except subprocess.CalledProcessError as error:
        typer.echo(
            f"error: ruff could not list the files: {str(error.stderr).strip()}",
            err = True,
        )
        raise typer.Exit(code = 1)

    # every project is checked before any file changes, so a run never stops half done
    problems = find_version_problems(files = files)
    if problems:
        for problem in problems:
            typer.echo(f"error: {problem}", err = True)
        raise typer.Exit(code = 1)

    results = format_files(paths = files, mode = mode, line_length = line_length)

    # color only for a person at a terminal, and never with NO_COLOR set (no-color.org);
    # color = True keeps click from stripping escape characters that are part of the source
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    for result in results:
        if result.error:
            typer.echo(f"error: {result.path}: {result.error}", err = True)
        elif result.diff:
            diff_text = color_diff(diff = result.diff) if use_color else result.diff
            typer.echo(diff_text, nl = False, color = True)
        elif result.changed:
            typer.echo(
                f"{'reformatted' if mode == Mode.WRITE else 'would reformat'} {result.path}",
            )

    # one project's warning comes from each of its files with SQL: show it once
    for warning in dict.fromkeys(
        result.warning
        for result in results
        if result.warning
    ):
        typer.echo(f"warning: {warning}", err = True)

    failed = any(result.error for result in results)
    changed = any(result.changed for result in results)

    raise typer.Exit(code = 1 if failed or (mode != Mode.WRITE and changed) else 0)


def color_diff(*, diff: str) -> str:
    """The diff with each line colored by its first characters, as git does."""
    return "".join(color_line(line = line) for line in diff.splitlines(keepends = True))


def color_line(*, line: str) -> str:
    """The file headers come before the single + and -, which they start with."""
    text = line.rstrip("\n")
    ending = line[len(text) :]

    if text.startswith(("+++", "---")):
        return typer.style(text, bold = True) + ending

    if text.startswith("@@"):
        return typer.style(text, fg = "cyan") + ending

    if text.startswith("+"):
        return typer.style(text, fg = "green") + ending

    if text.startswith("-"):
        return typer.style(text, fg = "red") + ending
    return line


def main() -> None:
    app()
