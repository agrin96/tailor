import os
import ast
import sys
import difflib
import tokenize
import subprocess
from enum import StrEnum
from pathlib import Path
import importlib.metadata
from typing import Annotated
from dataclasses import replace, dataclass
from concurrent.futures import ProcessPoolExecutor

import typer

from tailor.dialects import sql_dialect
from tailor.sql import format_sql_strings
from tailor.keywords import widen_keywords
from tailor.spacing import space_statements
from tailor.strings import close_class_docstrings
from tailor.ordering import order_type_aliases, order_class_members
from tailor.imports import same_meaning, sort_imports, first_party_names
from tailor.config import Settings, project_root, load_settings, project_python
from tailor.layout import ruff_binary, ruff_format, explode_brackets, restyle_brackets
from tailor.constants import (
    Marker,
    SQL_MARKER,
    DIFF_LINE_STYLES,
    DEFAULT_SQL_DIALECT,
    MAXIMUM_RUFF_PASSES,
    MINIMUM_PROJECT_PYTHON,
)


class Mode(StrEnum):
    WRITE = "write"
    CHECK = "check"
    DIFF = "diff"


@dataclass(frozen = True)
class FileResult:
    path: Path
    changed: bool
    error: str | None
    diff: str
    warning: str | None = None


def format_source(
    *,
    source: str,
    filename: str,
    first_party: frozenset[str],
    settings: Settings,
    sql_dialect: str,
) -> str:
    formatted = ruff_format(
        source = widen_keywords(source = source),
        line_length = settings.line_length,
        filename = filename,
        keep_trailing_commas = False,
    )

    exploded, brackets = explode_brackets(
        formatted = formatted,
        line_length = settings.line_length,
        filename = filename,
        use_split_markers = Marker.SPLIT not in formatted,
        passes_left = MAXIMUM_RUFF_PASSES,
    )

    restyled = restyle_brackets(source = exploded, brackets = brackets)
    sorted_imports = sort_imports(source = restyled, first_party = first_party)

    ordered = order_class_members(
        source = sorted_imports,
        constructors = settings.constructors,
    )
    aliased = order_type_aliases(source = ordered)
    closed = close_class_docstrings(
        source = aliased,
        line_length = settings.line_length,
    )

    result = space_statements(
        source = format_sql_strings(
            source = closed,
            line_length = settings.line_length,
            dialect = sql_dialect,
        ),
        settings = settings,
    )

    # ponytail: one check from ruff's output to the result, so a failure does not name the stage
    if not same_meaning(
        before = formatted.replace(Marker.WIDENER, ""),
        after = result,
        sql_dialect = sql_dialect,
    ):
        raise ValueError("house style changed what the code means, output discarded")
    return result


def python_files(*, paths: list[Path]) -> list[Path]:
    """The Python files ruff would check under the paths: ruff's default exclusions, the
    project's exclude settings and .gitignore apply. A file named directly is kept unless
    ruff's force-exclude is on, as with ruff format. Each file once, even when arguments
    overlap: two workers writing one file would lose the output."""
    listing = subprocess.run(
        [ruff_binary(), "check", "--show-files", *(str(path) for path in paths)],
        capture_output = True,
        text = True,
        check = True,
    ).stdout

    # keyed by the real file, so a folder and a symlink to it give one path, not two
    files: dict[Path, Path] = {}
    for line in listing.splitlines():
        if line.endswith(".py"):
            files.setdefault(Path(line).resolve(), Path(line))

    here = Path.cwd()
    return [
        file.relative_to(here) if file.is_relative_to(here) else file
        for file in files.values()
    ]


def format_file(*, path: Path, mode: Mode, line_length: int | None) -> FileResult:
    """Format one file. Changed means the file changed, or would change under check or diff.
    line_length, when given, replaces the one from pyproject.toml."""
    try:
        source = path.read_text()
        root = project_root(directory = path.resolve().parent)
        settings = load_settings(root = root)

        # only a file with SQL needs its dialect, and only such a file can warn about it
        dialect, warning = (
            sql_dialect(
                path = path,
                root = root,
                tree = ast.parse(source),
                settings = settings,
            )
            if SQL_MARKER in source
            else (DEFAULT_SQL_DIALECT, None)
        )

        result = format_source(
            source = source,
            filename = str(path),
            first_party = first_party_names(root = root),
            settings = replace(settings, line_length = line_length)
            if line_length
            else settings,
            sql_dialect = dialect,
        )
    except subprocess.CalledProcessError as error:
        return FileResult(
            path = path,
            changed = False,
            error = error.stderr.strip(),
            diff = "",
        )
    except (OSError, ValueError, SyntaxError, tokenize.TokenError) as error:
        return FileResult(path = path, changed = False, error = str(error), diff = "")

    changed = result != source
    if changed and mode == Mode.WRITE:
        path.write_text(result)

    diff = (
        unified_diff(before = source, after = result, name = str(path))
        if changed and mode == Mode.DIFF
        else ""
    )
    return FileResult(
        path = path,
        changed = changed,
        error = None,
        diff = diff,
        warning = warning,
    )


def colored_diff(*, diff: str) -> str:
    """The diff with each line colored by its first characters, as git does."""
    return "".join(
        colored_line(line = line)
        for line in diff.splitlines(keepends = True)
    )


def colored_line(*, line: str) -> str:
    style = next(
        (style for prefix, style in DIFF_LINE_STYLES if line.startswith(prefix)),
        None,
    )

    if style is None:
        return line

    text = line.rstrip("\n")
    return typer.style(text, **style) + line[len(text) :]


def unified_diff(*, before: str, after: str, name: str) -> str:
    """A patch that tools can apply: a last line without a newline gets the usual marker."""
    records = difflib.unified_diff(
        before.splitlines(keepends = True),
        after.splitlines(keepends = True),
        fromfile = name,
        tofile = name,
    )
    return "".join(
        record if record.endswith("\n") else record + "\n\\ No newline at end of file\n"
        for record in records
    )


def format_files(
    *,
    paths: list[Path],
    mode: Mode,
    line_length: int | None,
) -> list[FileResult]:
    if len(paths) == 1:
        return [format_file(path = paths[0], mode = mode, line_length = line_length)]

    with ProcessPoolExecutor(max_workers = os.process_cpu_count()) as executor:
        # largest first, so one big file does not finish alone at the end
        futures = {
            path: executor.submit(
                format_file,
                path = path,
                mode = mode,
                line_length = line_length,
            )
            for path in sorted(
                paths,
                key = lambda path: path.stat().st_size,
                reverse = True,
            )
        }
        return [futures[path].result() for path in paths]


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
        files = python_files(paths = paths)
    except subprocess.CalledProcessError as error:
        typer.echo(
            f"error: ruff could not list the files: {error.stderr.strip()}",
            err = True,
        )
        raise typer.Exit(code = 1)

    # every project is checked before any file changes, so a run never stops half done
    problems = version_problems(files = files)
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
            diff_text = colored_diff(diff = result.diff) if use_color else result.diff
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


def version_problems(*, files: list[Path]) -> list[str]:
    """One message for each configuration whose target Python tailor cannot format: older
    than MINIMUM_PROJECT_PYTHON, or newer than the Python tailor runs on."""
    targets = set()
    problems = []

    for directory in sorted({file.resolve().parent for file in files}):
        try:
            target = project_python(directory = directory)
        except ValueError as error:
            problems.append(str(error))
            continue

        if target is not None:
            targets.add(target)

    return (
        problems
        + [
            problem
            for version, source in sorted(targets)
            if (problem := version_problem(version = version, source = source))
        ]
    )


def version_problem(*, version: tuple[int, int], source: Path) -> str | None:
    running = sys.version_info[:2]
    if MINIMUM_PROJECT_PYTHON <= version <= running:
        return None

    target = f"{version[0]}.{version[1]}"
    if version < MINIMUM_PROJECT_PYTHON:
        oldest = ".".join(map(str, MINIMUM_PROJECT_PYTHON))
        return f"{source} targets Python {target}; tailor formats code that targets Python {oldest} or newer"

    return (
        f"{source} targets Python {target}, but tailor runs on Python {running[0]}.{running[1]}. "
        f"Reinstall it on a newer Python: uv tool install --python {target} --reinstall pytailor"
    )


def main() -> None:
    app()
