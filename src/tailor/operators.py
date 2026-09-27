"""Operator expressions: the marker that makes ruff put one operand per line, and where a
packed operator expression splits."""

import io
import re
import ast
import tokenize

from tailor.tokens import Edit, is_code, ends_value
from tailor.constants import (
    Marker,
    COMPARISONS,
    OPERATOR_TIERS,
    LOOSER_THAN_CONDITIONAL,
)


BINARY_OPERATORS = {
    operator: symbol
    for tier in OPERATOR_TIERS
    for operator, symbol in tier.items()
}

# longest symbol first, so `**` is not read as two `*`
SYMBOLS = sorted(BINARY_OPERATORS.values(), key = len, reverse = True)
OPERATOR_PATTERN = (
    rf"(?:\band\b|\bor\b|{'|'.join(re.escape(symbol) for symbol in SYMBOLS)})"
)

# how ruff splits inside an operand: a line with an operator that ends in an opening bracket,
# or a line that starts with a closing bracket followed by an operator
OPERAND_SPLIT = re.compile(
    rf"{OPERATOR_PATTERN}[^\n]*[(\[{{]$|^\s*[)\]}}][^\n]*?{OPERATOR_PATTERN}",
    re.MULTILINE,
)


def split_markers(*, source: str) -> list[Edit]:
    """Ruff lays out a statement like `x = a and f(...)` or `x = [...] + b` by splitting
    inside one operand. With a third operand it puts one operand per line instead, so add
    a marker operand. remove_split_markers takes it out once the layout is done."""
    if not OPERAND_SPLIT.search(source):
        return []

    lines = io.StringIO(source).readlines()
    expressions = [
        getattr(statement, field, None)
        for statement in ast.walk(ast.parse(source))
        if isinstance(statement, ast.stmt)
        for field in ("value", "test")
    ]

    marked = [
        (expression, operator)
        for expression in expressions
        if isinstance(expression, ast.BoolOp | ast.BinOp)
        and expression.lineno < expression.end_lineno
        for operator in [operator_text(expression = expression)]
        if not all(
            starts_line_after_operator(
                operand = operand,
                operator = operator,
                lines = lines,
            )
            for operand in later_operands(expression = expression)
        )
        # `**` groups to the right, so a marker added earlier can sit deeper than an operand
        and not any(
            isinstance(node, ast.Name) and node.id == Marker.SPLIT
            for node in ast.walk(expression)
        )
    ]

    return [
        Edit(
            row = expression.end_lineno,
            start_column = column,
            end_column = column,
            text = f" {operator} {Marker.SPLIT}",
        )
        for expression, operator in marked
        for column in [
            len(
                lines[expression.end_lineno - 1]
                .encode()[: expression.end_col_offset]
                .decode()
            )
        ]
    ]


def operator_text(*, expression: ast.BoolOp | ast.BinOp) -> str:
    if isinstance(expression, ast.BoolOp):
        return "and" if isinstance(expression.op, ast.And) else "or"
    return BINARY_OPERATORS[type(expression.op)]


def later_operands(*, expression: ast.BoolOp | ast.BinOp) -> list[ast.expr]:
    """Every operand after the first. `a + b + c` nests as `(a + b) + c`, so walk left."""
    if isinstance(expression, ast.BoolOp):
        return expression.values[1:]

    left = expression.left
    same_operator = isinstance(left, ast.BinOp) and type(left.op) is type(expression.op)
    return [
        *(later_operands(expression = left) if same_operator else []),
        expression.right,
    ]


def starts_line_after_operator(
    *,
    operand: ast.expr,
    operator: str,
    lines: list[str],
) -> bool:
    """True in the one-operand-per-line layout: only the operator stands before the operand."""
    line = lines[operand.lineno - 1]
    prefix = line.encode()[: operand.col_offset].decode()
    return prefix.strip() == operator


def remove_split_markers(*, source: str) -> str:
    """Take out each marker with its operator, spaced or not: ruff writes `a**b` compact."""
    marker = rf"\s*{OPERATOR_PATTERN}\s*{Marker.SPLIT}"
    marker_line = re.compile(rf"^{marker}\s*$")

    unmarked = "".join(
        line
        for line in io.StringIO(source).readlines()
        if not marker_line.match(line)
    )
    return re.sub(marker, "", unmarked)


def expression_break_indexes(*, children: tuple[tokenize.TokenInfo, ...]) -> list[int]:
    """The children that start a new line when a parenthesized expression splits: the if/else
    of a conditional, else each and/or, else each operator of the loosest tier present."""
    if any(
        child.string in LOOSER_THAN_CONDITIONAL
        and child.type in {tokenize.OP, tokenize.NAME}
        for child in children
    ):
        return []

    # a conditional binds looser than and/or, so its `if` and `else` split first
    conditional_indexes = [
        index
        for index, child in enumerate(children)
        if is_code(token = child, string = "if")
        or is_code(token = child, string = "else")
    ]

    if conditional_indexes:
        return conditional_indexes

    boolean_indexes = (
        [
            index
            for index, child in enumerate(children)
            if is_code(token = child, string = "or")
        ]
        or [
            index
            for index, child in enumerate(children)
            if is_code(token = child, string = "and")
        ]
    )

    if boolean_indexes:
        return boolean_indexes

    if any(
        child.string in COMPARISONS and child.type in {tokenize.OP, tokenize.NAME}
        for child in children
    ):
        return []

    return next(
        (
            indexes
            for tier in OPERATOR_TIERS
            if (
                indexes := operator_indexes(
                    children = children,
                    operators = tuple(tier.values()),
                )
            )
        ),
        [],
    )


def operator_indexes(
    *,
    children: tuple[tokenize.TokenInfo, ...],
    operators: tuple[str, ...],
) -> list[int]:
    """Where the given binary operators stand among the children. A `-` or `+` that follows
    another operator is a sign, not a binary operator."""
    return [
        index
        for index, child in enumerate(children)
        if index > 0
        and child.type in {tokenize.OP, tokenize.NAME}
        and child.string in operators
        and ends_value(token = children[index - 1])
    ]
