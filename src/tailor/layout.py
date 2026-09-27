"""The ruff loop: format, add trailing commas and operator markers, format again, then split
the lines ruff packed and restore the widened keywords."""

import io
import subprocess
from dataclasses import dataclass

from ruff.__main__ import find_ruff_bin

from tailor.constants import Marker
from tailor.operators import (
    insert_split_markers,
    remove_split_markers,
    parenthesize_conditionals,
    find_expression_break_indexes,
)
from tailor.tokens import (
    Edit,
    Bracket,
    is_hugged,
    apply_edits,
    find_brackets,
    follows_value,
    is_explodable,
    is_comprehension,
    find_comprehension_clause_indexes,
)


@dataclass(frozen = True)
class ExplodedSource:
    """source: the code after the last ruff run. brackets: the brackets in that code."""
    source: str
    brackets: list[Bracket]


def ruff_format(
    *,
    source: str,
    line_length: int,
    filename: str,
    keep_trailing_commas: bool = True,
) -> str:
    """keep_trailing_commas False lays the code out from scratch: a comma this tool added
    on an earlier run must not keep a bracket split after the code got shorter."""
    # ponytail: one ruff process per file per pass (~4 ms each), batching means copying ruff's config discovery
    command = [
        find_ruff_bin(),
        "format",
        "--line-length",
        str(line_length),
        "--config",
        f"format.skip-magic-trailing-comma = {str(not keep_trailing_commas).lower()}",
        "--stdin-filename",
        filename,
        "-",
    ]
    return subprocess.run(
        command,
        input = source,
        capture_output = True,
        text = True,
        check = True,
    ).stdout


def explode_brackets(
    *,
    formatted: str,
    line_length: int,
    filename: str,
    use_split_markers: bool,
    passes_left: int,
) -> ExplodedSource:
    """Add a magic trailing comma to every bracket ruff packed onto one indented line, mark
    operator expressions ruff split inside one operand, put split conditionals in
    parentheses, and run ruff again. Repeat until nothing is left to explode.
    Pass use_split_markers False when the marker name is already in the code: removing
    markers would then also remove the code's own text. passes_left caps the ruff runs:
    in a region ruff leaves alone (fmt: off) a layout may never settle."""
    brackets = find_brackets(source = formatted)
    explodable = [bracket for bracket in brackets if is_explodable(bracket = bracket)]
    hugged = [bracket for bracket in explodable if is_hugged(bracket = bracket)]

    commas = [
        Edit(
            row = bracket.children[-1].end[0],
            start_column = bracket.children[-1].end[1],
            end_column = bracket.children[-1].end[1],
            text = ",",
        )
        for bracket in hugged
    ]

    # markers go in alone: a comma added now would explode an operand that fits once split
    markers = insert_split_markers(source = formatted) if use_split_markers else []
    edits = markers or parenthesize_conditionals(source = formatted) or commas

    is_done = not edits or not passes_left
    if is_done and use_split_markers and Marker.SPLIT in formatted:
        unmarked = remove_split_markers(source = formatted)
        return ExplodedSource(
            source = unmarked,
            brackets = find_brackets(source = unmarked),
        )

    if is_done:
        return ExplodedSource(source = formatted, brackets = brackets)

    reformatted = ruff_format(
        source = apply_edits(source = formatted, edits = edits),
        line_length = line_length,
        filename = filename,
    )
    return explode_brackets(
        formatted = reformatted,
        line_length = line_length,
        filename = filename,
        use_split_markers = use_split_markers,
        passes_left = passes_left - 1,
    )


def restyle_brackets(*, source: str, brackets: list[Bracket]) -> str:
    """Give each for/if clause of a wrapped comprehension, and each operand of a wrapped
    operator expression, its own line. Turn every widened keyword back into `name = `: the
    same width, so no column moves."""
    lines = io.StringIO(source).readlines()

    # a comment after the packed line is often why ruff kept it apart: splitting the line
    # would leave the comment on the last piece only
    line_breaks = [
        edit
        for bracket in brackets
        if not bracket.in_template
        and is_hugged(bracket = bracket)
        and not lines[bracket.children[-1].end[0] - 1][bracket.children[-1].end[1] :]
        .lstrip()
        .startswith("#")
        for edit in build_line_break_edits(
            bracket = bracket,
            lines = lines,
            indexes = find_break_indexes(bracket = bracket),
        )
    ]

    spaced = apply_edits(source = source, edits = line_breaks).replace(
        Marker.WIDENER + "=",
        " = ",
    )
    # a region ruff leaves alone (fmt: off) keeps the spacing it was written with
    return spaced.replace(Marker.WIDENER, "")


def find_break_indexes(*, bracket: Bracket) -> list[int]:
    """The children that start a new line when the bracket's one packed line is split."""
    if is_comprehension(bracket = bracket):
        return find_comprehension_clause_indexes(children = bracket.children)

    if bracket.opener.string == "(" and not follows_value(bracket = bracket):
        return find_expression_break_indexes(children = bracket.children)

    return []


def build_line_break_edits(
    *,
    bracket: Bracket,
    lines: list[str],
    indexes: list[int],
) -> list[Edit]:
    children = bracket.children
    indent = lines[children[0].start[0] - 1][: children[0].start[1]]
    return [
        Edit(
            row = children[index].start[0],
            start_column = children[index - 1].end[1],
            end_column = children[index].start[1],
            text = "\n" + indent,
        )
        for index in indexes
    ]
