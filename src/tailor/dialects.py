"""Which SQL dialect a file's `--sql` strings are in: set in pyproject.toml, or found from
the project's SQL linter configuration, the drivers the file imports, or the drivers the
project depends on."""

import re
import ast
import tomllib
import functools
import configparser
from pathlib import Path

from tailor.config import Settings
from tailor.constants import (
    SQL_DRIVER_MODULES,
    DEFAULT_SQL_DIALECT,
    SQL_DRIVER_PACKAGES,
)


def sql_dialect(
    *,
    path: Path,
    root: Path,
    tree: ast.Module,
    settings: Settings,
) -> tuple[str, str | None]:
    """The dialect, and a warning when the project's drivers disagree and nothing settles
    it. In order: [tool.tailor] sql-dialect, the dialect of the nearest .sqruff or .sqlfluff
    file, the one driver the file imports, the one driver the project depends on, SQLite."""
    if settings.sql_dialect:
        return settings.sql_dialect, None

    configured = linter_dialect(directory = path.resolve().parent)
    if configured:
        return configured, None

    imported = imported_dialects(tree = tree)
    if len(imported) == 1:
        return next(iter(imported)), None

    declared = declared_dialects(root = root)
    if len(declared) == 1:
        return next(iter(declared)), None

    if len(declared | imported) > 1:
        found = ", ".join(sorted(declared | imported))
        return DEFAULT_SQL_DIALECT, (
            f"{root / 'pyproject.toml'}: the project uses more than one database ({found}), "
            f"so tailor reads its SQL as {DEFAULT_SQL_DIALECT}. "
            "Set sql-dialect in [tool.tailor] to choose."
        )

    return DEFAULT_SQL_DIALECT, None


@functools.cache
def linter_dialect(*, directory: Path) -> str | None:
    """The dialect of the nearest .sqruff or .sqlfluff file, as those tools read it."""
    for folder in (directory, *directory.parents):
        for name, section in ((".sqruff", "sqruff"), (".sqlfluff", "sqlfluff")):
            config_path = folder / name
            if config_path.is_file():
                parser = configparser.ConfigParser()
                parser.read(config_path)
                dialect = parser.get(section, "dialect", fallback = None)
                if dialect:
                    return dialect

    return None


def imported_dialects(*, tree: ast.Module) -> set[str]:
    """The dialects of the database drivers the file imports, anywhere in it."""
    modules = [
        module
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for module in imported_modules(node = node)
    ]
    return {
        dialect
        for module in modules
        for driver, dialect in SQL_DRIVER_MODULES.items()
        if module == driver or module.startswith(driver + ".")
    }


def imported_modules(*, node: ast.Import | ast.ImportFrom) -> list[str]:
    """`import a.b` gives a.b; `from a import b` gives a and a.b, so either spelling of a
    driver like google.cloud.bigquery counts."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    base = node.module or ""
    return [base, *(f"{base}.{alias.name}" for alias in node.names)]


@functools.cache
def declared_dialects(*, root: Path) -> frozenset[str]:
    """The dialects of the drivers the project's pyproject.toml depends on, in its
    dependencies, optional dependencies, and dependency groups."""
    path = root / "pyproject.toml"
    document = tomllib.loads(path.read_text()) if path.is_file() else {}
    project = document.get("project", {})

    requirements = [
        *project.get("dependencies", []),
        *(
            item
            for group in project.get("optional-dependencies", {}).values()
            for item in group
        ),
        *(
            item
            for group in document.get("dependency-groups", {}).values()
            for item in group
        ),
    ]

    # a dependency group can include another group as a table, which names no package
    names = {
        package_name(requirement = requirement)
        for requirement in requirements
        if isinstance(requirement, str)
    }
    return frozenset(
        SQL_DRIVER_PACKAGES[name]
        for name in names
        if name in SQL_DRIVER_PACKAGES
    )


def package_name(*, requirement: str) -> str:
    """`psycopg[binary]>=3.1` gives psycopg; names compare lower-case with dashes."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[._]+", "-", match.group(1).lower()) if match else ""
