"""Keyword widening: ruff lays out lines as wide as they are once `name = value` is written."""

import tokenize
import itertools

from tailor.constants import Marker, BracketText
from tailor.tokens import (
    Edit,
    is_code,
    apply_edits,
    code_tokens,
    find_brackets,
    TEMPLATE_DEPTH_CHANGE,
)


def widen_keywords(*, source: str) -> str:
    """Add Marker.WIDENER to each keyword argument and unannotated default name, lambda defaults
    included, so ruff lays out every line at the width it has once `name = ` is written."""
    if Marker.WIDENER in source:
        raise ValueError(
            f"the file contains '{Marker.WIDENER}', which the formatter reserves",
        )

    keyword_names = [
        bracket.children[index - 1]
        for bracket in find_brackets(source = source)
        if bracket.opener.string == "(" and not bracket.in_template
        for index, child in enumerate(bracket.children)
        if is_code(token = child, string = "=")
        and (index == 1 or is_code(token = bracket.children[index - 2], string = ","))
    ]

    # a lambda inside a call can be found both ways: one edit per name
    names = {
        name.end: name
        for name in keyword_names + lambda_default_names(source = source)
        if name.type == tokenize.NAME
    }

    edits = [
        Edit(
            row = row,
            start_column = column,
            end_column = column,
            text = Marker.WIDENER,
        )
        for row, column in names
    ]
    return apply_edits(source = source, edits = edits)


def lambda_default_names(*, source: str) -> list[tokenize.TokenInfo]:
    """The name before each `=` among a lambda's parameters, up to the lambda's own colon.
    A lambda inside an f-string or t-string is left alone: in `{...=}` the spacing is text."""
    tokens = code_tokens(source = source)
    template_depths = list(
        itertools.accumulate(
            TEMPLATE_DEPTH_CHANGE.get(token.type, 0)
            for token in tokens
        )
    )
    names = []
    for index, token in enumerate(tokens):
        if template_depths[index] or not is_code(token = token, string = "lambda"):
            continue

        depth = 0

        # a lambda in a default has a colon of its own, which comes before ours
        nested_lambdas = 0

        for position in range(index + 1, len(tokens)):
            current = tokens[position]
            if current.type == tokenize.OP and current.string in BracketText.OPENERS:
                depth += 1
            elif current.type == tokenize.OP and current.string in BracketText.CLOSERS:
                depth -= 1
            elif depth == 0 and is_code(token = current, string = "lambda"):
                nested_lambdas += 1
            elif depth == 0 and is_code(token = current, string = ":"):
                if not nested_lambdas:
                    break

                nested_lambdas -= 1
            elif (
                depth == 0
                and not nested_lambdas
                and is_code(token = current, string = "=")
            ):
                names.append(tokens[position - 1])

    return names
