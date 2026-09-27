"""Docstrings: a class docstring ends on its last line of text."""

import io
import ast

from tailor.constants import TRIPLE_QUOTES
from tailor.syntax import is_docstring, find_last_row


def close_class_docstrings(*, source: str, line_length: int) -> str:
    """Move a class docstring's closing quotes up onto its last line of text, when that line
    still fits. A line ending in a backslash or a quote keeps them apart."""
    lines = io.StringIO(source).readlines()
    docstrings = [
        node.body[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and is_docstring(statement = node.body[0])
    ]

    # bottom up, so an edit never moves the rows of a docstring still ahead
    for docstring in sorted(
        docstrings,
        key = lambda docstring: docstring.lineno,
        reverse = True,
    ):
        last_row = find_last_row(node = docstring)
        closing = lines[last_row - 1].strip()

        if docstring.lineno == last_row or closing not in TRIPLE_QUOTES:
            continue

        text_row = next(
            row
            for row in range(last_row - 1, docstring.lineno - 1, -1)
            if lines[row - 1].strip()
        )
        text = lines[text_row - 1].rstrip()
        if len(text) + len(closing) > line_length or text.endswith(("\\", closing[0])):
            continue

        lines[text_row - 1 : last_row] = [text + closing + "\n"]

    return "".join(lines)
