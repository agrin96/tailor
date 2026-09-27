"""Small questions about statements and rows, shared by the spacing, import and order passes."""

import ast
from typing import TypeGuard

from tailor.constants import MemberKind, PROPERTY_DECORATORS


def is_docstring(*, statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def find_last_row(*, node: ast.stmt | ast.expr) -> int:
    """The last row of a parsed node. The ast module types it as optional, but the parser
    always sets it."""
    if node.end_lineno is None:
        raise ValueError(f"a {type(node).__name__} from the parser has no end row")
    return node.end_lineno


def find_first_row(*, statement: ast.stmt) -> int:
    """First row of the statement, including its decorators."""
    return min(
        [statement.lineno]
        + [decorator.lineno for decorator in getattr(statement, "decorator_list", [])]
    )


def find_leading_row(*, row: int, floor_row: int, lines: list[str]) -> int:
    """The row, moved up over the comments right above it, never to floor_row."""
    while row - 1 > floor_row and lines[row - 2].lstrip().startswith("#"):
        row -= 1
    return row


def is_dunder_definition(
    *,
    statement: ast.stmt,
) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    return (
        isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        and len(statement.name) > 4
        and statement.name.startswith("__")
        and statement.name.endswith("__")
    )


def is_short_dunder(*, statement: ast.stmt, lines: list[str], most_lines: int) -> bool:
    """A dunder whose body has at most most_lines lines that are not blank."""
    if not is_dunder_definition(statement = statement):
        return False

    rows = range(statement.body[0].lineno, find_last_row(node = statement) + 1)
    return sum(1 for row in rows if lines[row - 1].strip()) <= most_lines


def find_body_end_row(*, member: ast.stmt, lines: list[str]) -> int:
    """The last row of the member, including comments after it that sit deeper than the
    member itself: those end its body."""
    end_row = find_last_row(node = member)
    for row in range(find_last_row(node = member) + 1, len(lines) + 1):
        line = lines[row - 1]
        if not line.strip():
            continue

        is_deeper_comment = (
            line.lstrip().startswith("#")
            and len(line) - len(line.lstrip()) > member.col_offset
        )

        if not is_deeper_comment:
            break

        end_row = row

    return end_row


def classify_member(*, member: ast.stmt) -> MemberKind | None:
    """The group a class member moves with, or None when it keeps its place."""
    if is_dunder_definition(statement = member):
        return MemberKind.DUNDER

    if not isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
        return None

    names = {
        read_decorator_name(decorator = decorator)
        for decorator in member.decorator_list
    }

    if "classmethod" in names:
        return MemberKind.CLASSMETHOD

    if names & set(PROPERTY_DECORATORS):
        return MemberKind.PROPERTY

    return None


def read_decorator_name(*, decorator: ast.expr) -> str | None:
    """`classmethod` for @classmethod, `setter` for @name.setter; None for a call."""
    match decorator:
        case ast.Name(id = name) | ast.Attribute(attr = name):
            return name
        case _:
            return None
