import os
import sys
import difflib
import argparse
import tokenize
import subprocess
from enum import StrEnum
from pathlib import Path
from dataclasses import replace, dataclass
from concurrent.futures import ProcessPoolExecutor

from tailor.keywords import widen_keywords
from tailor.spacing import space_statements
from tailor.ordering import order_class_members
from tailor.constants import Marker, MAXIMUM_RUFF_PASSES
from tailor.config import Settings, project_root, load_settings
from tailor.imports import same_meaning, sort_imports, first_party_names
from tailor.layout import ruff_format, explode_brackets, restyle_brackets


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
    """Each file once, as first spelled, even when arguments overlap: two workers
    writing one file would lose the output."""
    files = [
        file
        for path in paths
        for file in (sorted(path.rglob("*.py")) if path.is_dir() else [path])
        if not any(
            part.startswith(".") or part == "__pycache__"
            for part in file.parts[len(path.parts) :]
        )
    ]
    unique = {}
    for file in files:
        unique.setdefault(file.resolve(), file)
    return list(unique.values())


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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog = "tailor",
        description = "ruff format, then house style",
    )
    parser.add_argument("paths", nargs = "+", type = Path)
    parser.add_argument(
        "--line-length",
        type = int,
        help = "replaces line-length from [tool.tailor] or [tool.ruff] in pyproject.toml",
    )
    dry_run = parser.add_mutually_exclusive_group()
    dry_run.add_argument(
        "--check",
        action = "store_true",
        help = "list files that would change, write nothing",
    )

    dry_run.add_argument(
        "--diff",
        action = "store_true",
        help = "show the changes as a diff, write nothing",
    )
    arguments = parser.parse_args()
    mode = (
        Mode.DIFF
        if arguments.diff
        else Mode.CHECK
        if arguments.check
        else Mode.WRITE
    )

    results = format_files(
        paths = python_files(paths = arguments.paths),
        mode = mode,
        line_length = arguments.line_length,
    )

    for result in results:
        if result.error:
            print(f"error: {result.path}: {result.error}", file = sys.stderr)
        elif result.diff:
            sys.stdout.write(result.diff)
        elif result.changed:
            print(
                f"{'reformatted' if mode == Mode.WRITE else 'would reformat'} {result.path}",
            )

    failed = any(result.error for result in results)
    changed = any(result.changed for result in results)

    sys.exit(1 if failed or (mode != Mode.WRITE and changed) else 0)
