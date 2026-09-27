"""The pipeline: find the files, run ruff and each house-style pass on them, check the
meaning, and write or report the result."""

import os
import ast
import difflib
import tokenize
import subprocess
from enum import StrEnum
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor

from ruff.__main__ import find_ruff_bin

from tailor.sql import format_sql_strings
from tailor.keywords import widen_keywords
from tailor.spacing import space_statements
from tailor.strings import close_class_docstrings
from tailor.dialects import DialectChoice, detect_sql_dialect
from tailor.ordering import order_type_aliases, order_class_members
from tailor.config import Settings, load_settings, find_project_root
from tailor.layout import ruff_format, explode_brackets, restyle_brackets
from tailor.constants import Marker, SQL_MARKER, SqlDialect, MAXIMUM_RUFF_PASSES
from tailor.imports import sort_imports, have_same_meaning, list_first_party_names


class Mode(StrEnum):
    """WRITE changes the files. CHECK lists the files that would change. DIFF shows how."""
    WRITE = "write"
    CHECK = "check"
    DIFF = "diff"


@dataclass(frozen = True)
class FileResult:
    """path: the file. changed: the file changed, or would change under check or diff.
    error: why the file was not formatted, or None. diff: the change as a patch, in diff
    mode. warning: a note about the file's SQL dialect, or None."""
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
    sql_dialect: SqlDialect,
) -> str:
    formatted = ruff_format(
        source = widen_keywords(source = source),
        line_length = settings.line_length,
        filename = filename,
        keep_trailing_commas = False,
    )

    exploded = explode_brackets(
        formatted = formatted,
        line_length = settings.line_length,
        filename = filename,
        use_split_markers = Marker.SPLIT not in formatted,
        passes_left = MAXIMUM_RUFF_PASSES,
    )

    restyled = restyle_brackets(source = exploded.source, brackets = exploded.brackets)
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
            indent_width = settings.sql_indent_width,
        ),
        settings = settings,
    )

    # ponytail: one check from ruff's output to the result, so a failure does not name the stage
    keeps_meaning = have_same_meaning(
        before = formatted.replace(Marker.WIDENER, ""),
        after = result,
        sql_dialect = sql_dialect,
    )

    if not keeps_meaning:
        raise ValueError("house style changed what the code means, output discarded")
    return result


def list_python_files(*, paths: list[Path]) -> list[Path]:
    """The Python files ruff would check under the paths: ruff's default exclusions, the
    project's exclude settings and .gitignore apply. A file named directly is kept unless
    ruff's force-exclude is on, as with ruff format. Each file once, even when arguments
    overlap: two workers writing one file would lose the output."""
    listing = subprocess.run(
        [find_ruff_bin(), "check", "--show-files", *(str(path) for path in paths)],
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
    """Format one file. line_length, when given, replaces the one from pyproject.toml."""
    try:
        source = path.read_text()
        root = find_project_root(directory = path.resolve().parent)
        settings = load_settings(root = root)

        # only a file with SQL needs its dialect, and only such a file can warn about it
        choice = (
            detect_sql_dialect(
                path = path,
                root = root,
                tree = ast.parse(source),
                settings = settings,
            )
            if SQL_MARKER in source
            else DialectChoice(dialect = SqlDialect.SQLITE)
        )

        result = format_source(
            source = source,
            filename = str(path),
            first_party = list_first_party_names(root = root),
            settings = (
                settings.model_copy(update = {"line_length": line_length})
                if line_length
                else settings
            ),
            sql_dialect = choice.dialect,
        )
    except subprocess.CalledProcessError as error:
        return FileResult(
            path = path,
            changed = False,
            error = str(error.stderr).strip(),
            diff = "",
        )
    except (OSError, ValueError, SyntaxError, tokenize.TokenError) as error:
        return FileResult(path = path, changed = False, error = str(error), diff = "")

    changed = result != source
    if changed and mode == Mode.WRITE:
        path.write_text(result)

    diff = (
        build_unified_diff(before = source, after = result, name = str(path))
        if changed and mode == Mode.DIFF
        else ""
    )
    return FileResult(
        path = path,
        changed = changed,
        error = None,
        diff = diff,
        warning = choice.warning,
    )


def build_unified_diff(*, before: str, after: str, name: str) -> str:
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
