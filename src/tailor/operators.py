"""Operator expressions: the marker that makes ruff put one operand per line, and where a
packed operator expression splits."""

import io
import re
import ast
import tokenize

from tailor.syntax import find_last_row
from tailor.tokens import Edit, is_code, ends_value, list_code_tokens
from tailor.constants import (
    Marker,
    BracketText,
    COMPARISONS,
    OPERATOR_TIERS,
    LOOSER_THAN_CONDITIONAL,
)


BINARY_OPERATORS: dict[type[ast.operator], str] = {
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


def insert_split_markers(*, source: str) -> list[Edit]:
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
        and expression.lineno < find_last_row(node = expression)
        for operator in [read_operator_text(expression = expression)]
        if not all(
            starts_line_after_operator(
                operand = operand,
                operator = operator,
                lines = lines,
            )
            for operand in list_later_operands(expression = expression)
        )
        # `**` groups to the right, so a marker added earlier can sit deeper than an operand
        and not any(
            isinstance(node, ast.Name) and node.id == Marker.SPLIT
            for node in ast.walk(expression)
        )
    ]

    return [
        Edit(
            row = find_last_row(node = expression),
            start_column = column,
            end_column = column,
            text = f" {operator} {Marker.SPLIT}",
        )
        for expression, operator in marked
        for column in [
            len(
                lines[find_last_row(node = expression) - 1]
                .encode()[: expression.end_col_offset]
                .decode()
            )
        ]
    ]


def parenthesize_conditionals(*, source: str) -> list[Edit]:
    """Ruff splits a conditional expression in place when it is a dict value, a keyword
    argument or one of several items, so its `if` and `else` line up with the items around
    it. Put each split conditional in parentheses of its own, and ruff gives it a block of
    its own. A conditional alone in a bracket has that block already. The tail of a chained
    conditional stays with its chain, and a conditional in an f-string or t-string keeps
    its text, which `{value=}` prints."""
    tree = ast.parse(source)
    lines = io.StringIO(source).readlines()

    chained = {
        id(node.orelse)
        for node in ast.walk(tree)
        if isinstance(node, ast.IfExp)
    }

    templated = {
        id(inner)
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr | ast.TemplateStr)
        for inner in ast.walk(node)
    }

    conditionals = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.IfExp)
        and node.lineno < find_last_row(node = node)
        and id(node) not in chained | templated
    ]

    tokens = list_code_tokens(source = source)
    first_indexes = {token.start: index for index, token in enumerate(tokens)}
    last_indexes = {token.end: index for index, token in enumerate(tokens)}

    edits: list[Edit] = []
    for conditional in conditionals:
        start_row = conditional.lineno
        end_row = find_last_row(node = conditional)
        start_line = lines[start_row - 1]

        end_line = lines[end_row - 1]
        start_column = len(start_line.encode()[: conditional.col_offset].decode())
        end_column = len(end_line.encode()[: conditional.end_col_offset].decode())

        # code tokens only: a comment between the bracket and the conditional is not code
        before = tokens[first_indexes[(start_row, start_column)] - 1]
        following = tokens[last_indexes[(end_row, end_column)] + 1 :]
        after = following[1] if following[0].string == "," else following[0]

        is_alone_in_brackets = (
            before.string in BracketText.OPENERS
            and after.string in BracketText.CLOSERS
        )

        if is_alone_in_brackets:
            continue

        edits += [
            Edit(
                row = start_row,
                start_column = start_column,
                end_column = start_column,
                text = "(",
            ),
            Edit(
                row = end_row,
                start_column = end_column,
                end_column = end_column,
                text = ")",
            ),
        ]

    return edits


def read_operator_text(*, expression: ast.BoolOp | ast.BinOp) -> str:
    match expression:
        case ast.BoolOp(op = ast.And()):
            return "and"
        case ast.BoolOp():
            return "or"
        case ast.BinOp():
            return BINARY_OPERATORS[type(expression.op)]


def list_later_operands(*, expression: ast.BoolOp | ast.BinOp) -> list[ast.expr]:
    """Every operand after the first. `a + b + c` nests as `(a + b) + c`, so walk left."""
    match expression:
        case ast.BoolOp():
            return expression.values[1:]
        case ast.BinOp(left = ast.BinOp() as left) if type(left.op) is type(
            expression.op,
        ):
            return [*list_later_operands(expression = left), expression.right]
        case ast.BinOp():
            return [expression.right]


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


def find_expression_break_indexes(
    *,
    children: tuple[tokenize.TokenInfo, ...],
) -> list[int]:
    """The children that start a new line when a parenthesized expression splits: the if/else
    of a conditional, else each and/or, else each operator of the loosest tier present."""
    has_looser_operator = any(
        child.string in LOOSER_THAN_CONDITIONAL
        and child.type in {tokenize.OP, tokenize.NAME}
        for child in children
    )

    if has_looser_operator:
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

    has_comparison = any(
        child.string in COMPARISONS and child.type in {tokenize.OP, tokenize.NAME}
        for child in children
    )

    if has_comparison:
        return []

    for tier in OPERATOR_TIERS:
        indexes = find_operator_indexes(
            children = children,
            operators = tuple(tier.values()),
        )

        if indexes:
            return indexes

    return []


def find_operator_indexes(
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
