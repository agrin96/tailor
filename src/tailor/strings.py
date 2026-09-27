"""Docstrings: a class docstring ends on its last line of text."""

import io
import ast

from tailor.syntax import is_docstring
from tailor.constants import TRIPLE_QUOTES


def close_class_docstrings(*, source: str, line_length: int) -> str:
    """Move a class docstring's closing quotes up onto its last line of text, when that line
    still fits. A line ending in a backslash or a quote keeps them apart."""
    lines = io.StringIO(source).readlines()
    docstrings = [
        node.body[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef) and is_docstring(statement = node.body[0])
    ]
    edits = []
    for docstring in docstrings:
        closing = lines[docstring.end_lineno - 1].strip()
        if docstring.lineno == docstring.end_lineno or closing not in TRIPLE_QUOTES:
            continue

        text_row = next(
            row
            for row in range(docstring.end_lineno - 1, docstring.lineno - 1, -1)
            if lines[row - 1].strip()
        )
        text = lines[text_row - 1].rstrip()
        if len(text) + len(closing) > line_length or text.endswith(("\\", closing[0])):
            continue

        edits.append((text_row, docstring.end_lineno, text + closing + "\n"))

    for first_row, last_row, line in sorted(edits, reverse = True):
        lines[first_row - 1 : last_row] = [line]

    return "".join(lines)
