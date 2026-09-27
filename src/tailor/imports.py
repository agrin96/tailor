import io
import ast
import sys
import functools
import itertools
from enum import IntEnum
from pathlib import Path
from dataclasses import dataclass

from tailor.config import PythonVersion
from tailor.sql import build_sql_signature
from tailor.constants import SQL_MARKER, SqlDialect
from tailor.ordering import AliasBlock, plan_alias_block
from tailor.syntax import is_docstring, find_last_row, classify_member


class ImportGroup(IntEnum):
    """The import groups, in their order at the top of a file."""
    FUTURE = 0
    STANDARD_LIBRARY = 1
    THIRD_PARTY = 2
    LOCAL = 3


@dataclass(frozen = True)
class ImportEntry:
    """group: the group the import sorts into. text: the import as it is written out."""
    group: ImportGroup
    text: str


@dataclass(frozen = True, order = True)
class ImportedName:
    """One name an import binds: from the module, at the relative level, as the alias."""
    module: str
    level: int
    name: str
    alias: str


@dataclass(frozen = True)
class CodeMeaning:
    """imports: every name the top-level imports bind, sorted. rest: the other statements,
    with the changes the house style is allowed to make undone."""
    imports: list[ImportedName]
    rest: ast.Module


@functools.cache
def list_first_party_names(*, root: Path) -> frozenset[str]:
    """Top level packages and modules in the project root, or in its src/."""
    return frozenset(
        child.stem
        for package_directory in (root, root / "src")
        if package_directory.is_dir()
        for child in package_directory.iterdir()
        if child.stem.isidentifier() and (child.is_dir() or child.suffix == ".py")
    )


def sort_imports(*, source: str, first_party: frozenset[str]) -> str:
    """Group the leading imports (stdlib, third party, local), shortest first, multiline last."""
    tree = ast.parse(source)
    lines = io.StringIO(source).readlines()

    body = (
        tree.body[1:]
        if tree.body and is_docstring(statement = tree.body[0])
        else tree.body
    )

    leading = itertools.takewhile(
        lambda statement: isinstance(statement, ast.Import | ast.ImportFrom),
        body,
    )

    block = [
        statement
        for statement in leading
        if isinstance(statement, ast.Import | ast.ImportFrom)
    ]

    if not block:
        return source

    first_row = block[0].lineno
    last_row = find_last_row(node = block[-1])

    statement_rows = {
        row
        for statement in block
        for row in range(statement.lineno, find_last_row(node = statement) + 1)
    }

    # comments between imports: the author placed them, keep the order
    has_comments = any(
        lines[row - 1].strip()
        for row in range(first_row, last_row + 1)
        if row not in statement_rows
    )

    if has_comments:
        return source

    # two imports bind one name: the last one wins, so the order matters
    if has_repeated_binding(block = block):
        return source

    entries = [
        entry
        for statement in block
        for entry in build_import_entries(
            statement = statement,
            lines = lines,
            first_party = first_party,
        )
    ]

    texts_by_group = {
        group: sorted(
            (entry.text for entry in entries if entry.group == group),
            key = lambda text: ("\n" in text, len(text), text),
        )
        for group in ImportGroup
    }

    sorted_block = (
        "\n\n".join("\n".join(texts) for texts in texts_by_group.values() if texts)
        + "\n"
    )
    return "".join(lines[: first_row - 1]) + sorted_block + "".join(lines[last_row:])


def has_repeated_binding(*, block: list[ast.Import | ast.ImportFrom]) -> bool:
    """A plain `import a.b` always binds the package `a`, so repeats among those are harmless.
    Any other name bound twice, or bound by both kinds, makes the order matter."""
    packages = {
        alias.name.split(".")[0]
        for statement in block
        if isinstance(statement, ast.Import)
        for alias in statement.names
        if not alias.asname
    }

    others = [
        alias.asname or alias.name
        for statement in block
        for alias in statement.names
        if isinstance(statement, ast.ImportFrom) or alias.asname
    ]
    return (
        "*" in others
        or len(others) != len(set(others))
        or not packages.isdisjoint(others)
    )


def build_import_entries(
    *,
    statement: ast.Import | ast.ImportFrom,
    lines: list[str],
    first_party: frozenset[str],
) -> list[ImportEntry]:
    """`import a, b` splits into one entry per module. A statement with a comment stays as
    written."""
    original = "".join(
        lines[statement.lineno - 1 : find_last_row(node = statement)],
    ).rstrip()

    match statement:
        case ast.ImportFrom():
            group = classify_import(
                module = statement.module or "",
                level = statement.level,
                first_party = first_party,
            )

            text = write_from_import(
                statement = statement,
                lines = lines,
                original = original,
            )
            return [ImportEntry(group = group, text = text)]
        case ast.Import() if "#" in original:
            group = classify_import(
                module = statement.names[0].name,
                level = 0,
                first_party = first_party,
            )
            return [ImportEntry(group = group, text = original)]
        case ast.Import():
            return [
                ImportEntry(
                    group = classify_import(
                        module = alias.name,
                        level = 0,
                        first_party = first_party,
                    ),
                    text = f"import {alias.name}"
                    + (f" as {alias.asname}" if alias.asname else ""),
                )
                for alias in statement.names
            ]


def classify_import(
    *,
    module: str,
    level: int,
    first_party: frozenset[str],
) -> ImportGroup:
    top_level = module.split(".")[0]
    if level:
        return ImportGroup.LOCAL

    if top_level == "__future__":
        return ImportGroup.FUTURE

    if top_level in sys.stdlib_module_names:
        return ImportGroup.STANDARD_LIBRARY

    if top_level in first_party:
        return ImportGroup.LOCAL
    return ImportGroup.THIRD_PARTY


def write_from_import(
    *,
    statement: ast.ImportFrom,
    lines: list[str],
    original: str,
) -> str:
    """The statement with its names sorted shortest first, or as written if it has comments."""
    if "#" in original:
        return original

    names = sorted(
        (
            alias.name + (f" as {alias.asname}" if alias.asname else "")
            for alias in statement.names
        ),
        key = lambda name: (len(name), name),
    )
    head = f"from {'.' * statement.level}{statement.module or ''} import"
    if statement.lineno == find_last_row(node = statement):
        return f"{head} {', '.join(names)}"

    indent = lines[statement.lineno][
        : len(lines[statement.lineno]) - len(lines[statement.lineno].lstrip())
    ]
    return f"{head} (\n" + "".join(f"{indent}{name},\n" for name in names) + ")"


def have_same_meaning(
    *,
    before: str,
    after: str,
    sql_dialect: SqlDialect,
    target: PythonVersion | None,
) -> bool:
    """True when both sources have the same top-level imports, in any order, and the
    same other statements, in order. Positions are ignored."""
    before_meaning = extract_meaning(
        source = before,
        sql_dialect = sql_dialect,
        target = target,
    )

    after_meaning = extract_meaning(
        source = after,
        sql_dialect = sql_dialect,
        target = target,
    )

    return (
        before_meaning.imports == after_meaning.imports
        and ast.compare(before_meaning.rest, after_meaning.rest)
    )


def extract_meaning(
    *,
    source: str,
    sql_dialect: SqlDialect,
    target: PythonVersion | None,
) -> CodeMeaning:
    body = ast.parse(source).body
    match plan_alias_block(body = body, target = target):
        case AliasBlock(dependencies = dependencies):
            moved = dependencies
        case _:
            moved = []

    imports = [
        statement
        for statement in body
        if isinstance(statement, ast.Import | ast.ImportFrom)
    ]

    imported = sorted(
        ImportedName(
            module = getattr(statement, "module", None) or "",
            level = getattr(statement, "level", 0),
            name = alias.name,
            alias = alias.asname or "",
        )
        for statement in imports
        for alias in statement.names
    )

    rest = ast.Module(
        body = [statement for statement in body if statement not in imports],
        type_ignores = [],
    )

    # the order passes move type aliases and grouped class members; the string pass changes
    # trailing whitespace in docstrings and the SQL layout, not its tokens; none of these count
    aliases = [
        statement
        for statement in rest.body
        if isinstance(statement, ast.TypeAlias)
    ]

    # aliases of different names may trade places; two of one name keep their order; the
    # definitions the aliases need lead, in written order
    sorted_aliases = sorted(aliases, key = lambda alias: alias.name.id)
    rest.body = [
        *moved,
        *(
            statement
            for statement in rest.body
            if statement not in aliases and statement not in moved
        ),
        *sorted_aliases,
    ]

    for node in ast.walk(rest):
        if isinstance(node, ast.ClassDef):
            moved = [
                statement
                for statement in node.body
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
                and classify_member(member = statement) is not None
            ]

            sorted_moved = sorted(
                moved,
                key = lambda member: (classify_member(member = member), member.name),
            )

            node.body = [
                *(statement for statement in node.body if statement not in moved),
                *sorted_moved,
            ]

        if isinstance(
            node,
            ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ):
            match node.body:
                case [
                    ast.Expr(value = ast.Constant(value = str() as text) as constant),
                    *_,
                ]:
                    constant.value = text.rstrip()
                case _:
                    pass

        match node:
            case ast.Constant(value = str() as text) if text.startswith(SQL_MARKER):
                node.value = repr(
                    build_sql_signature(
                        sql = text.removeprefix(SQL_MARKER),
                        dialect = sql_dialect,
                    )
                )
            case _:
                pass

    return CodeMeaning(imports = imported, rest = rest)
