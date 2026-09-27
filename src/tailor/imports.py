import io
import ast
import sys
import functools
import itertools
from enum import IntEnum
from pathlib import Path

from tailor.syntax import is_docstring, is_dunder_definition


class ImportGroup(IntEnum):
    FUTURE = 0
    STANDARD_LIBRARY = 1
    THIRD_PARTY = 2
    LOCAL = 3


@functools.cache
def first_party_names(*, root: Path) -> frozenset[str]:
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

    block = list(
        itertools.takewhile(
            lambda statement: isinstance(statement, ast.Import | ast.ImportFrom),
            body,
        )
    )

    if not block:
        return source

    first_row = block[0].lineno
    last_row = block[-1].end_lineno

    statement_rows = {
        row
        for statement in block
        for row in range(statement.lineno, statement.end_lineno + 1)
    }

    # comments between imports: the author placed them, keep the order
    if any(
        lines[row - 1].strip()
        for row in range(first_row, last_row + 1)
        if row not in statement_rows
    ):
        return source

    # two imports bind one name: the last one wins, so the order matters
    if has_repeated_binding(block = block):
        return source

    entries = [
        entry
        for statement in block
        for entry in import_entries(
            statement = statement,
            lines = lines,
            first_party = first_party,
        )
    ]

    texts_by_group = {
        group: sorted(
            (text for entry_group, text in entries if entry_group == group),
            key = lambda text: ("\n" in text, len(text), text),
        )
        for group in ImportGroup
    }

    sorted_block = (
        "\n\n".join("\n".join(texts) for texts in texts_by_group.values() if texts)
        + "\n"
    )
    result = "".join(lines[: first_row - 1]) + sorted_block + "".join(lines[last_row:])
    return result


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


def import_entries(
    *,
    statement: ast.Import | ast.ImportFrom,
    lines: list[str],
    first_party: frozenset[str],
) -> list[tuple[ImportGroup, str]]:
    """Group and text of the statement. `import a, b` splits into one entry per module."""
    original = "".join(lines[statement.lineno - 1 : statement.end_lineno]).rstrip()
    if isinstance(statement, ast.ImportFrom):
        group = import_group(
            module = statement.module or "",
            level = statement.level,
            first_party = first_party,
        )
        return [
            (
                group,
                from_import_text(
                    statement = statement,
                    lines = lines,
                    original = original,
                ),
            )
        ]

    if "#" in original:
        group = import_group(
            module = statement.names[0].name,
            level = 0,
            first_party = first_party,
        )
        return [(group, original)]

    return [
        (
            import_group(module = alias.name, level = 0, first_party = first_party),
            f"import {alias_text(alias=alias)}",
        )
        for alias in statement.names
    ]


def import_group(
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


def from_import_text(
    *,
    statement: ast.ImportFrom,
    lines: list[str],
    original: str,
) -> str:
    """The statement with its names sorted shortest first, or as written if it has comments."""
    if "#" in original:
        return original

    names = sorted(
        (alias_text(alias = alias) for alias in statement.names),
        key = lambda name: (len(name), name),
    )
    head = f"from {'.' * statement.level}{statement.module or ''} import"
    if statement.lineno == statement.end_lineno:
        return f"{head} {', '.join(names)}"

    indent = lines[statement.lineno][
        : len(lines[statement.lineno]) - len(lines[statement.lineno].lstrip())
    ]
    return f"{head} (\n" + "".join(f"{indent}{name},\n" for name in names) + ")"


def same_meaning(*, before: str, after: str) -> bool:
    """True when both sources have the same top-level imports, in any order, and the
    same other statements, in order. Positions are ignored."""
    before_imports, before_rest = meaning(source = before)
    after_imports, after_rest = meaning(source = after)
    return (
        before_imports == after_imports
        and ast.dump(before_rest) == ast.dump(after_rest)
    )


def meaning(*, source: str) -> tuple[list[tuple[str, int, str, str]], ast.Module]:
    body = ast.parse(source).body
    imports = [
        statement
        for statement in body
        if isinstance(statement, ast.Import | ast.ImportFrom)
    ]

    imported = sorted(
        (
            getattr(statement, "module", None) or "",
            getattr(statement, "level", 0),
            alias.name,
            alias.asname or "",
        )
        for statement in imports
        for alias in statement.names
    )

    rest = ast.Module(
        body = [statement for statement in body if statement not in imports],
        type_ignores = [],
    )

    # the order pass moves dunders inside a class, so their order does not count
    for node in ast.walk(rest):
        if isinstance(node, ast.ClassDef):
            node.body = [
                *(
                    statement
                    for statement in node.body
                    if not is_dunder_definition(statement = statement)
                ),
                *sorted(
                    (
                        statement
                        for statement in node.body
                        if is_dunder_definition(statement = statement)
                    ),
                    key = lambda statement: statement.name,
                ),
            ]

    return imported, rest


def alias_text(*, alias: ast.alias) -> str:
    return alias.name + (f" as {alias.asname}" if alias.asname else "")
