"""Small questions about statements and rows, shared by the spacing, import and order passes."""

import ast


def is_docstring(*, statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def first_row(*, statement: ast.stmt) -> int:
    """First row of the statement, including its decorators."""
    return min(
        [statement.lineno]
        + [decorator.lineno for decorator in getattr(statement, "decorator_list", [])]
    )


def leading_row(*, row: int, floor_row: int, lines: list[str]) -> int:
    """The row, moved up over the comments right above it, never to floor_row."""
    while row - 1 > floor_row and lines[row - 2].lstrip().startswith("#"):
        row -= 1
    return row


def is_dunder_definition(*, statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        and len(statement.name) > 4
        and statement.name.startswith("__")
        and statement.name.endswith("__")
    )


def content_length(
    *,
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
) -> int:
    """Non-blank lines of the body. Blank lines are left out, so the spacing pass cannot change it."""
    rows = range(statement.body[0].lineno, statement.end_lineno + 1)
    return sum(1 for row in rows if lines[row - 1].strip())


def is_short_dunder(*, statement: ast.stmt, lines: list[str], most_lines: int) -> bool:
    return (
        is_dunder_definition(statement = statement)
        and content_length(statement = statement, lines = lines) <= most_lines
    )


def body_end_row(*, member: ast.stmt, lines: list[str]) -> int:
    """The last row of the member, including comments after it that sit deeper than the
    member itself: those end its body."""
    end_row = member.end_lineno
    for row in range(member.end_lineno + 1, len(lines) + 1):
        line = lines[row - 1]
        if not line.strip():
            continue

        if (
            not line.lstrip().startswith("#")
            or len(line) - len(line.lstrip()) <= member.col_offset
        ):
            break

        end_row = row

    return end_row
