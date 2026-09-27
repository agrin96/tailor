"""The formatter's settings, read from [tool.tailor] in the nearest pyproject.toml, and the
Python version each project targets."""

import re
import sys
import tomllib
import datetime
import functools
from pathlib import Path
from typing import Annotated
from dataclasses import dataclass

from annotated_types import Ge, Gt
from pydantic import Strict, BaseModel, ConfigDict, ValidationError

from tailor.constants import SqlDialect, MINIMUM_PROJECT_PYTHON


type TomlValue = (
    str
    | int
    | float
    | bool
    | datetime.datetime
    | datetime.date
    | datetime.time
    | list[TomlValue]
    | TomlTable
)
type PythonVersion = tuple[int, int]
type WholeNumber = Annotated[int, Strict(), Ge(0)]
type PositiveWholeNumber = Annotated[int, Strict(), Gt(0)]

type TomlTable = dict[str, TomlValue]


class Settings(BaseModel):
    """The [tool.tailor] options, with kebab-case keys like ruff's.
    line_length: the maximum line length.
    module_definition_blank_lines: blank lines around top-level functions and classes.
    class_definition_blank_lines: blank lines around methods and nested classes.
    compact_class_lines: a class with this many lines or fewer has no blank lines inside.
    assignment_group_size: the group size for a long run of assignments.
    long_body_lines: a for, while or if with a longer body gets a blank line after it.
    short_dunder_lines: dunders with a body this short have 1 blank line between them.
    constructors: the dunders that come first in a class.
    sql_dialect: the dialect of `--sql` strings; None finds it for each file.
    sql_indent_width: the indent of a SQL clause or subquery."""
    model_config = ConfigDict(
        frozen = True,
        extra = "forbid",
        alias_generator = lambda name: name.replace("_", "-"),
        validate_by_name = True,
        validate_by_alias = True,
    )

    line_length: PositiveWholeNumber = 88
    module_definition_blank_lines: WholeNumber = 2
    class_definition_blank_lines: WholeNumber = 2
    compact_class_lines: WholeNumber = 5
    assignment_group_size: PositiveWholeNumber = 3
    long_body_lines: WholeNumber = 2
    short_dunder_lines: WholeNumber = 2
    constructors: tuple[str, ...] = ("__new__", "__init__", "__post_init__")
    sql_dialect: SqlDialect | None = None
    sql_indent_width: PositiveWholeNumber = 2


@dataclass(frozen = True, order = True)
class PythonTarget:
    """version: the oldest Python a project targets. source: the file that says so."""
    version: PythonVersion
    source: Path


@dataclass(frozen = True)
class RuffConfig:
    """path: the file ruff reads. settings: its ruff table."""
    path: Path
    settings: TomlTable


@functools.cache
def find_project_root(*, directory: Path) -> Path:
    """The nearest folder with a pyproject.toml, or the directory itself."""
    return next(
        (
            folder
            for folder in (directory, *directory.parents)
            if (folder / "pyproject.toml").exists()
        ),
        directory,
    )


@functools.cache
def load_settings(*, root: Path) -> Settings:
    """[tool.tailor] from the root's pyproject.toml, with kebab-case keys like ruff's.
    Without a line-length of its own, tailor takes the one from [tool.ruff]."""
    path = root / "pyproject.toml"
    document = tomllib.loads(path.read_text()) if path.exists() else {}
    tools = read_table(parent = document, key = "tool", name = "tool", path = path)

    written = read_table(
        parent = tools,
        key = "tailor",
        name = "tool.tailor",
        path = path,
    )

    ruff = read_table(parent = tools, key = "ruff", name = "tool.ruff", path = path)
    inherited = (
        {"line-length": ruff["line-length"]}
        if not {"line-length", "line_length"} & written.keys() and "line-length" in ruff
        else {}
    )

    try:
        return Settings.model_validate(written | inherited)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, problem['loc']))}: {problem['msg']}"
            for problem in error.errors()
        )
        raise ValueError(f"{path}: [tool.tailor] {problems}") from error


@functools.cache
def find_project_python(*, directory: Path) -> PythonTarget | None:
    """The oldest Python the code in the directory targets, found the way ruff finds it: the
    target-version of the nearest ruff configuration (.ruff.toml, ruff.toml, or a
    pyproject.toml with [tool.ruff]), else the lower bound of the nearest [project]
    requires-python. None when neither is declared."""
    folders = (directory, *directory.parents)
    nearest_ruff = next(
        (config for folder in folders if (config := read_ruff_config(folder = folder))),
        None,
    )

    configured = (
        read_inherited_target(
            path = nearest_ruff.path,
            settings = nearest_ruff.settings,
        )
        if nearest_ruff
        else None
    )

    if configured:
        return configured

    for folder in folders:
        path = folder / "pyproject.toml"
        document = tomllib.loads(path.read_text()) if path.exists() else {}

        requires = read_table(
            parent = document,
            key = "project",
            name = "project",
            path = path,
        ).get("requires-python")

        if isinstance(requires, str):
            bound = parse_lower_bound(requires = requires)
            return PythonTarget(version = bound, source = path) if bound else None

    return None


def read_inherited_target(*, path: Path, settings: TomlTable) -> PythonTarget | None:
    """The target-version of a ruff configuration, or of the one it extends, and so on up
    its `extend` chain, as ruff inherits it. A chain that loops back ends the search."""
    visited: set[Path] = set()
    while path not in visited:
        visited.add(path)
        if "target-version" in settings:
            return PythonTarget(
                version = parse_target_version(
                    value = settings["target-version"],
                    path = path,
                ),
                source = path,
            )

        extended = settings.get("extend")
        if not isinstance(extended, str):
            return None

        path = (path.parent / Path(extended).expanduser()).resolve()
        if not path.exists():
            raise ValueError(
                f"{path}: the ruff configuration extends it, but it does not exist",
            )

        document = tomllib.loads(path.read_text())
        tools = read_table(parent = document, key = "tool", name = "tool", path = path)

        settings = (
            read_table(parent = tools, key = "ruff", name = "tool.ruff", path = path)
            if path.name == "pyproject.toml"
            else document
        )

    return None


def read_ruff_config(*, folder: Path) -> RuffConfig | None:
    """The ruff settings a folder holds, as ruff reads them: .ruff.toml, then ruff.toml,
    then the [tool.ruff] table of pyproject.toml."""
    for name in (".ruff.toml", "ruff.toml"):
        path = folder / name
        if path.exists():
            return RuffConfig(path = path, settings = tomllib.loads(path.read_text()))

    path = folder / "pyproject.toml"
    tools = read_table(
        parent = tomllib.loads(path.read_text()) if path.exists() else {},
        key = "tool",
        name = "tool",
        path = path,
    )

    if "ruff" not in tools:
        return None

    return RuffConfig(
        path = path,
        settings = read_table(
            parent = tools,
            key = "ruff",
            name = "tool.ruff",
            path = path,
        ),
    )


def parse_lower_bound(*, requires: str) -> PythonVersion | None:
    """The oldest Python 3 minor version a requires-python allows. Every clause must hold,
    so the largest lower bound wins; `>3.10` allows 3.11 at the least."""
    bounds: list[int] = []
    for clause in requires.split(","):
        match = re.fullmatch(r"\s*(>=|>|~=|==)\s*3\.(\d+)(\.[\d*]+)?\s*", clause)
        if match is None:
            continue

        operator, minor, patch = match.groups()
        bounds.append(
            int(minor) + 1 if operator == ">" and patch is None else int(minor),
        )

    return (3, max(bounds)) if bounds else None


def parse_target_version(*, value: TomlValue, path: Path) -> PythonVersion:
    """Ruff's `py312` spelling as (3, 12)."""
    match = re.fullmatch(r"py3(\d+)", value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"{path}: target-version must look like py312, not {value!r}")
    return 3, int(match.group(1))


def read_table(*, parent: TomlTable, key: str, name: str, path: Path) -> TomlTable:
    """The sub-table, or an empty one when it is missing. Anything else is an error."""
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{path}: [{name}] must be a table")
    return value


def find_version_problems(*, files: list[Path]) -> list[str]:
    """One message for each configuration whose target Python tailor cannot format: older
    than MINIMUM_PROJECT_PYTHON, or newer than the Python tailor runs on."""
    targets: set[PythonTarget] = set()
    problems: list[str] = []

    for directory in sorted({file.resolve().parent for file in files}):
        try:
            target = find_project_python(directory = directory)
        except ValueError as error:
            problems.append(str(error))
            continue

        if target is not None:
            targets.add(target)

    return (
        problems
        + [
            problem
            for target in sorted(targets)
            if (problem := find_version_problem(target = target))
        ]
    )


def find_version_problem(*, target: PythonTarget) -> str | None:
    running = sys.version_info[:2]
    if MINIMUM_PROJECT_PYTHON <= target.version <= running:
        return None

    version = f"{target.version[0]}.{target.version[1]}"
    if target.version < MINIMUM_PROJECT_PYTHON:
        oldest = ".".join(map(str, MINIMUM_PROJECT_PYTHON))
        return f"{target.source} targets Python {version}; tailor formats code that targets Python {oldest} or newer"

    return (
        f"{target.source} targets Python {version}, but tailor runs on Python {running[0]}.{running[1]}. "
        f"Reinstall it on a newer Python: uv tool install --python {version} --reinstall pytailor"
    )
