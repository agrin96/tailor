"""The formatter's settings, read from [tool.tailor] in the nearest pyproject.toml."""

import tomllib
import functools
from pathlib import Path
from dataclasses import fields, dataclass

from tailor.constants import POSITIVE_OPTIONS


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

    minimum = 1 if name in POSITIVE_OPTIONS else 0
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(
            f"{path}: [tool.tailor] {key} must be a whole number of at least {minimum}",
        )
