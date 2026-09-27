import os
import sys
import difflib
import tokenize
import subprocess
from enum import StrEnum
from pathlib import Path
from dataclasses import replace, dataclass
from concurrent.futures import ProcessPoolExecutor

import click

from tailor.keywords import widen_keywords
from tailor.spacing import space_statements
from tailor.ordering import order_class_members
from tailor.config import Settings, project_root, load_settings
from tailor.imports import same_meaning, sort_imports, first_party_names
from tailor.constants import Marker, DIFF_LINE_STYLES, MAXIMUM_RUFF_PASSES
from tailor.layout import ruff_binary, ruff_format, explode_brackets, restyle_brackets


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


def format_source(
    *,
    source: str,
    filename: str,
    first_party: frozenset[str],
    settings: Settings,
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
    result = space_statements(source = ordered, settings = settings)

    # ponytail: one check from ruff's output to the result, so a failure does not name the stage
    if not same_meaning(before = formatted.replace(Marker.WIDENER, ""), after = result):
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

        result = format_source(
            source = source,
            filename = str(path),
            first_party = first_party_names(root = root),
            settings = replace(settings, line_length = line_length)
            if line_length
            else settings,
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
    return FileResult(path = path, changed = changed, error = None, diff = diff)


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
    return click.style(text, **style) + line[len(text) :]


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

    with ProcessPoolExecutor(max_workers = os.cpu_count()) as executor:
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


@click.command(help = "ruff format, then house style")
@click.argument(
    "paths",
    nargs = -1,
    required = True,
    type = click.Path(exists = True, path_type = Path),
)
@click.option(
    "--line-length",
    type = click.IntRange(min = 1),
    help = "Replaces line-length from [tool.tailor] or [tool.ruff] in pyproject.toml.",
)
@click.option(
    "--check",
    is_flag = True,
    help = "List the files that would change, write nothing.",
)
@click.option(
    "--diff",
    is_flag = True,
    help = "Show the changes as a diff, write nothing.",
)
@click.version_option(package_name = "pytailor")
def main(
    *,
    paths: tuple[Path, ...],
    line_length: int | None,
    check: bool,
    diff: bool,
) -> None:
    if check and diff:
        raise click.UsageError("--check and --diff cannot be used together")

    mode = Mode.DIFF if diff else Mode.CHECK if check else Mode.WRITE
    try:
        files = python_files(paths = list(paths))
    except subprocess.CalledProcessError as error:
        raise click.ClickException(
            f"ruff could not list the files: {error.stderr.strip()}",
        )

    results = format_files(paths = files, mode = mode, line_length = line_length)

    # color only for a person at a terminal, and never with NO_COLOR set (no-color.org);
    # color = True keeps click from stripping escape characters that are part of the source
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    for result in results:
        if result.error:
            click.echo(f"error: {result.path}: {result.error}", err = True)
        elif result.diff:
            diff = colored_diff(diff = result.diff) if use_color else result.diff
            click.echo(diff, nl = False, color = True)
        elif result.changed:
            click.echo(
                f"{'reformatted' if mode == Mode.WRITE else 'would reformat'} {result.path}",
            )

    failed = any(result.error for result in results)
    changed = any(result.changed for result in results)

    sys.exit(1 if failed or (mode != Mode.WRITE and changed) else 0)
