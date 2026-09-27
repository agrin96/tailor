"""Which SQL dialect a file's `--sql` strings are in: set in pyproject.toml, or found from
the project's SQL linter configuration, the drivers the file imports, or the drivers the
project depends on."""

import re
import ast
import tomllib
import functools
import configparser
from pathlib import Path
from dataclasses import dataclass

from tailor.config import Settings, TomlValue, read_table
from tailor.constants import SqlDialect, SQL_DRIVER_MODULES, SQL_DRIVER_PACKAGES


@dataclass(frozen = True)
class DialectChoice:
    """dialect: the dialect of the file's SQL. warning: why the choice is a guess, or None."""
    dialect: SqlDialect
    warning: str | None = None


def detect_sql_dialect(
    *,
    path: Path,
    root: Path,
    tree: ast.Module,
    settings: Settings,
) -> DialectChoice:
    """In order: [tool.tailor] sql-dialect, the dialect of the nearest .sqruff or .sqlfluff
    file, the one driver the file imports, the one driver the project depends on, SQLite.
    When the project's drivers disagree and nothing settles it, the choice has a warning."""
    if settings.sql_dialect:
        return DialectChoice(dialect = settings.sql_dialect)

    configured = read_linter_dialect(directory = path.resolve().parent)
    if configured:
        return DialectChoice(dialect = configured)

    imported = find_imported_dialects(tree = tree)
    if len(imported) == 1:
        return DialectChoice(dialect = next(iter(imported)))

    declared = read_declared_dialects(root = root)
    if len(declared) == 1:
        return DialectChoice(dialect = next(iter(declared)))

    if len(declared | imported) > 1:
        found = ", ".join(sorted(declared | imported))
        return DialectChoice(
            dialect = SqlDialect.SQLITE,
            warning = (
                f"{root / 'pyproject.toml'}: the project uses more than one database ({found}), "
                f"so tailor reads its SQL as {SqlDialect.SQLITE}. "
                "Set sql-dialect in [tool.tailor] to choose."
            ),
        )

    return DialectChoice(dialect = SqlDialect.SQLITE)


@functools.cache
def read_linter_dialect(*, directory: Path) -> SqlDialect | None:
    """The dialect of the nearest .sqruff or .sqlfluff file, as those tools read it. A
    dialect that sqruff does not know counts as none."""
    for folder in (directory, *directory.parents):
        for name, section in ((".sqruff", "sqruff"), (".sqlfluff", "sqlfluff")):
            config_path = folder / name
            if not config_path.is_file():
                continue

            parser = configparser.ConfigParser()
            parser.read(config_path)
            dialect = parser.get(section, "dialect", fallback = None)
            if dialect:
                return SqlDialect(dialect) if dialect in SqlDialect else None

    return None


def find_imported_dialects(*, tree: ast.Module) -> set[SqlDialect]:
    """The dialects of the database drivers the file imports, anywhere in it."""
    modules = [
        module
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for module in list_imported_modules(node = node)
    ]
    return {
        dialect
        for module in modules
        for driver, dialect in SQL_DRIVER_MODULES.items()
        if module == driver or module.startswith(driver + ".")
    }


def list_imported_modules(*, node: ast.Import | ast.ImportFrom) -> list[str]:
    """`import a.b` gives a.b; `from a import b` gives a and a.b, so either spelling of a
    driver like google.cloud.bigquery counts."""
    match node:
        case ast.Import():
            return [alias.name for alias in node.names]
        case ast.ImportFrom():
            base = node.module or ""
            return [base, *(f"{base}.{alias.name}" for alias in node.names)]


@functools.cache
def read_declared_dialects(*, root: Path) -> frozenset[SqlDialect]:
    """The dialects of the drivers the project's pyproject.toml depends on, in its
    dependencies, optional dependencies, and dependency groups. `psycopg[binary]>=3.1`
    names psycopg: package names compare in lower case, with dashes."""
    path = root / "pyproject.toml"
    document = tomllib.loads(path.read_text()) if path.is_file() else {}

    project = read_table(
        parent = document,
        key = "project",
        name = "project",
        path = path,
    )

    optional = read_table(
        parent = project,
        key = "optional-dependencies",
        name = "project.optional-dependencies",
        path = path,
    )

    groups = read_table(
        parent = document,
        key = "dependency-groups",
        name = "dependency-groups",
        path = path,
    )

    lists: list[TomlValue] = [
        project.get("dependencies", []),
        *optional.values(),
        *groups.values(),
    ]

    requirements = [
        item
        for group in lists
        if isinstance(group, list)
        for item in group
    ]

    # a dependency group can include another group as a table, which names no package
    names = {
        re.sub(r"[._]+", "-", match.group(1).lower())
        for requirement in requirements
        if isinstance(requirement, str)
        for match in [re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)]
        if match
    }
    return frozenset(
        SQL_DRIVER_PACKAGES[name]
        for name in names
        if name in SQL_DRIVER_PACKAGES
    )
