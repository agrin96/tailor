"""The formatter's settings, read from [tool.tailor] in the nearest pyproject.toml."""

import re
import tomllib
import functools
from pathlib import Path
from dataclasses import fields, dataclass

from tailor.constants import SQRUFF_DIALECTS, POSITIVE_OPTIONS


@dataclass(frozen = True)
class Settings:
    line_length: int = 88
    module_definition_blank_lines: int = 2
    class_definition_blank_lines: int = 2
    compact_class_lines: int = 5
    assignment_group_size: int = 3
    long_body_lines: int = 2
    short_dunder_lines: int = 2
    constructors: tuple[str, ...] = ("__new__", "__init__", "__post_init__")
    sql_dialect: str | None = None


@functools.cache
def project_root(*, directory: Path) -> Path:
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
    tools = table(parent = document, key = "tool", name = "tool", path = path)
    written = table(parent = tools, key = "tailor", name = "tool.tailor", path = path)

    ruff = table(parent = tools, key = "ruff", name = "tool.ruff", path = path)
    known = {field.name for field in fields(Settings)}
    unknown = sorted(key for key in written if key.replace("-", "_") not in known)

    if unknown:
        raise ValueError(f"{path}: unknown [tool.tailor] option: {', '.join(unknown)}")

    options = {key.replace("-", "_"): value for key, value in written.items()}

    if "line_length" not in options and "line-length" in ruff:
        options["line_length"] = ruff["line-length"]

    for name, value in options.items():
        check_option(name = name, value = value, path = path)

    if "constructors" in options:
        options["constructors"] = tuple(options["constructors"])
    return Settings(**options)


@functools.cache
def project_python(*, directory: Path) -> tuple[tuple[int, int], Path] | None:
    """The oldest Python the code in the directory targets, and the file that says so, found
    the way ruff finds it: the target-version of the nearest ruff configuration (.ruff.toml,
    ruff.toml, or a pyproject.toml with [tool.ruff]), else the lower bound of the nearest
    [project] requires-python. None when neither is declared."""
    folders = (directory, *directory.parents)
    nearest_ruff = next(
        (config for folder in folders if (config := ruff_config(folder = folder))),
        None,
    )

    configured = (
        inherited_target(path = nearest_ruff[0], settings = nearest_ruff[1])
        if nearest_ruff
        else None
    )

    if configured:
        return configured

    for folder in folders:
        path = folder / "pyproject.toml"
        document = tomllib.loads(path.read_text()) if path.exists() else {}

        requires = table(
            parent = document,
            key = "project",
            name = "project",
            path = path,
        ).get("requires-python")

        if requires is not None:
            bound = lower_bound(requires = requires)
            return (bound, path) if bound else None

    return None


def inherited_target(
    *,
    path: Path,
    settings: dict,
) -> tuple[tuple[int, int], Path] | None:
    """The target-version of a ruff configuration, or of the one it extends, and so on up
    its `extend` chain, as ruff inherits it. A chain that loops back ends the search."""
    visited = set()
    while path not in visited:
        visited.add(path)
        if "target-version" in settings:
            return target_version(value = settings["target-version"], path = path), path

        extended = settings.get("extend")
        if not isinstance(extended, str):
            return None

        path = (path.parent / Path(extended).expanduser()).resolve()
        if not path.exists():
            raise ValueError(
                f"{path}: the ruff configuration extends it, but it does not exist",
            )

        document = tomllib.loads(path.read_text())
        settings = (
            document.get("tool", {}).get("ruff", {})
            if path.name == "pyproject.toml"
            else document
        )

    return None


def ruff_config(*, folder: Path) -> tuple[Path, dict] | None:
    """The ruff settings a folder holds, as ruff reads them: .ruff.toml, then ruff.toml,
    then the [tool.ruff] table of pyproject.toml."""
    for name in (".ruff.toml", "ruff.toml"):
        path = folder / name
        if path.exists():
            return path, tomllib.loads(path.read_text())

    path = folder / "pyproject.toml"
    tools = table(
        parent = tomllib.loads(path.read_text()) if path.exists() else {},
        key = "tool",
        name = "tool",
        path = path,
    )
    return (
        (path, table(parent = tools, key = "ruff", name = "tool.ruff", path = path))
        if "ruff" in tools
        else None
    )


def lower_bound(*, requires: str) -> tuple[int, int] | None:
    """The oldest Python 3 minor version a requires-python allows. Every clause must hold,
    so the largest lower bound wins; `>3.10` allows 3.11 at the least."""
    bounds = []
    for clause in requires.split(","):
        match = re.fullmatch(r"\s*(>=|>|~=|==)\s*3\.(\d+)(\.[\d*]+)?\s*", clause)
        if match is None:
            continue

        operator, minor, patch = match.groups()
        bounds.append(
            int(minor) + 1 if operator == ">" and patch is None else int(minor),
        )

    return (3, max(bounds)) if bounds else None


def target_version(*, value: object, path: Path) -> tuple[int, int]:
    """Ruff's `py312` spelling as (3, 12)."""
    match = re.fullmatch(r"py3(\d+)", value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f"{path}: target-version must look like py312, not {value!r}")
    return 3, int(match.group(1))


def table(*, parent: dict, key: str, name: str, path: Path) -> dict:
    """The sub-table, or an empty one when it is missing. Anything else is an error."""
    value = parent.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{path}: [{name}] must be a table")
    return value


def check_option(*, name: str, value: object, path: Path) -> None:
    key = name.replace("_", "-")
    if name == "constructors":
        if (
            not isinstance(value, list)
            or not all(isinstance(item, str) for item in value)
        ):
            raise ValueError(f"{path}: [tool.tailor] {key} must be a list of names")
        return

    if name == "sql_dialect":
        if value not in SQRUFF_DIALECTS:
            raise ValueError(
                f"{path}: [tool.tailor] {key} must be one of {', '.join(SQRUFF_DIALECTS)}",
            )

        return

    minimum = 1 if name in POSITIVE_OPTIONS else 0
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(
            f"{path}: [tool.tailor] {key} must be a whole number of at least {minimum}",
        )
