import io
import ast
import itertools
from enum import StrEnum
from dataclasses import dataclass
from collections.abc import Iterator

from tailor.config import Settings
from tailor.constants import IMPORTS, DEFINITIONS, LOOPS_AND_BRANCHES
from tailor.syntax import (
    is_docstring,
    find_last_row,
    find_first_row,
    is_short_dunder,
    find_leading_row,
    find_body_end_row,
)


class Context(StrEnum):
    """What a body of statements belongs to: the module, a class, a function (at any
    depth), or another block outside a function."""
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    OTHER = "other"


@dataclass(frozen = True)
class Block:
    """body: sibling statements. context: what the body belongs to. compact: the body is
    of a class short enough to have no blank lines."""
    body: list[ast.stmt]
    context: Context
    compact: bool


@dataclass(frozen = True)
class GapEdit:
    """The blank lines between two statements. start_index and end_index: the line indexes
    the gap spans. blank_lines: how many blank lines replace it."""
    start_index: int
    end_index: int
    blank_lines: int


def space_statements(*, source: str, settings: Settings) -> str:
    """Set the blank lines between sibling statements."""
    lines = io.StringIO(source).readlines()
    all_blocks = list(
        walk_blocks(
            body = ast.parse(source).body,
            context = Context.MODULE,
            compact = False,
            lines = lines,
            settings = settings,
        )
    )

    edits = [
        edit
        for block in all_blocks
        for edit in build_gap_edits(block = block, lines = lines, settings = settings)
    ]

    case_edits = [
        edit
        for block in all_blocks
        for statement in block.body
        if isinstance(statement, ast.Match)
        for edit in build_case_gap_edits(match = statement, lines = lines)
    ]

    for edit in sorted(
        edits + case_edits,
        key = lambda edit: edit.start_index,
        reverse = True,
    ):
        lines[edit.start_index : edit.end_index] = ["\n"] * edit.blank_lines
    return "".join(lines)


def walk_blocks(
    *,
    body: list[ast.stmt],
    context: Context,
    compact: bool,
    lines: list[str],
    settings: Settings,
) -> Iterator[Block]:
    yield Block(body = body, context = context, compact = compact)
    for statement in body:
        child_context = choose_child_context(statement = statement, context = context)
        compact_child = (
            isinstance(statement, ast.ClassDef)
            and is_compact(statement = statement, lines = lines, settings = settings)
        )

        for child_body in list_child_bodies(statement = statement):
            yield from walk_blocks(
                body = child_body,
                context = child_context,
                compact = compact_child,
                lines = lines,
                settings = settings,
            )


def choose_child_context(*, statement: ast.stmt, context: Context) -> Context:
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return Context.FUNCTION
        case ast.ClassDef():
            return Context.CLASS
        case _ if context == Context.FUNCTION:
            return Context.FUNCTION
        case _:
            return Context.OTHER


def list_child_bodies(*, statement: ast.stmt) -> Iterator[list[ast.stmt]]:
    for field in ("body", "orelse", "finalbody"):
        value = getattr(statement, field, None)
        if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
            yield value

    for handler in getattr(statement, "handlers", []):
        yield handler.body

    for case in getattr(statement, "cases", []):
        yield case.body


def is_compact(
    *,
    statement: ast.ClassDef,
    lines: list[str],
    settings: Settings,
) -> bool:
    rows = range(
        find_first_row(statement = statement),
        find_last_row(node = statement) + 1,
    )
    return (
        sum(1 for row in rows if lines[row - 1].strip()) <= settings.compact_class_lines
    )


def build_gap_edits(
    *,
    block: Block,
    lines: list[str],
    settings: Settings,
) -> list[GapEdit]:
    body = block.body
    if block.context == Context.FUNCTION and is_docstring(statement = body[0]):
        body = body[1:]
    pairs = list(itertools.pairwise(body))

    # comments indented deeper than a statement end its body, so they count as part of it
    end_rows = [
        find_body_end_row(member = previous, lines = lines)
        for previous, _ in pairs
    ]

    start_rows = [
        find_leading_row(
            row = find_first_row(statement = current),
            floor_row = end_row,
            lines = lines,
        )
        for (_, current), end_row in zip(pairs, end_rows)
    ]

    gap_rows = [
        range(end_row + 1, start_row)
        for end_row, start_row in zip(end_rows, start_rows)
    ]

    breaks = find_assignment_breaks(
        body = body,
        gap_rows = gap_rows,
        group_size = settings.assignment_group_size,
    )

    first_definition = next(
        (statement for statement in body if isinstance(statement, DEFINITIONS)),
        None,
    )
    edits: list[GapEdit] = []

    for index, (previous, current) in enumerate(pairs):
        rows = gap_rows[index]

        # a comment between blank lines belongs to neither statement: keep the gap
        if any(lines[row - 1].strip() for row in rows):
            continue

        wanted = count_wanted_blank_lines(
            block = block,
            previous = previous,
            current = current,
            existing = len(rows),
            assignment_break = index in breaks,
            commented = start_rows[index] < find_first_row(statement = current),
            short_dunders = is_short_dunder(
                statement = previous,
                lines = lines,
                most_lines = settings.short_dunder_lines,
            )
            and is_short_dunder(
                statement = current,
                lines = lines,
                most_lines = settings.short_dunder_lines,
            ),
            opens_methods = current is first_definition
            and not isinstance(previous, DEFINITIONS),
            settings = settings,
        )

        if wanted != len(rows):
            edits.append(
                GapEdit(
                    start_index = end_rows[index],
                    end_index = start_rows[index] - 1,
                    blank_lines = wanted,
                )
            )

    return edits


def find_assignment_breaks(
    *,
    body: list[ast.stmt],
    gap_rows: list[range],
    group_size: int,
) -> set[int]:
    """Gaps that get a blank line: between the groups of a long assignment run, and after
    a run of two or more. Gap i sits between body[i] and body[i + 1]."""
    runs: list[list[int]] = []
    for index, statement in enumerate(body):
        if not is_simple_assignment(statement = statement):
            continue

        if runs and runs[-1][-1] == index - 1 and not gap_rows[index - 1]:
            runs[-1].append(index)
        else:
            runs.append([index])

    breaks: set[int] = set()
    for run in runs:
        group_ends = list(
            itertools.accumulate(
                split_into_group_sizes(count = len(run), group_size = group_size),
            )
        )
        breaks |= {run[0] + group_end - 1 for group_end in group_ends[:-1]}

        if len(run) >= 2 and run[-1] < len(body) - 1:
            breaks.add(run[-1])

    return breaks


def split_into_group_sizes(*, count: int, group_size: int) -> list[int]:
    """Below two groups' worth, one group. Then groups of group_size up to twice that less
    one, as even as possible: with 3, 5 stays one group and 7 is 4 + 3."""
    if count < 2 * group_size:
        return [count]

    groups = count // group_size
    size, larger = divmod(count, groups)
    return [size + 1] * larger + [size] * (groups - larger)


def count_wanted_blank_lines(
    *,
    block: Block,
    previous: ast.stmt,
    current: ast.stmt,
    existing: int,
    assignment_break: bool,
    commented: bool,
    short_dunders: bool,
    opens_methods: bool,
    settings: Settings,
) -> int:
    touches_definition = (
        isinstance(previous, DEFINITIONS)
        or isinstance(current, DEFINITIONS)
    )

    match block.context:
        case Context.MODULE if touches_definition or (
            isinstance(previous, IMPORTS)
            and not isinstance(current, IMPORTS)
        ):
            return settings.module_definition_blank_lines
        case Context.MODULE if assignment_break:
            return max(existing, 1)
        # a method right after the docstring is opens_methods: it gets its one blank line below
        case Context.CLASS if (
            previous is block.body[0]
            and is_docstring(statement = previous)
            and not isinstance(current, DEFINITIONS)
        ):
            return 0
        case Context.CLASS if short_dunders or opens_methods:
            return 1
        case Context.CLASS if touches_definition:
            return settings.class_definition_blank_lines
        case _ if has_long_body(
            statement = previous,
            most_lines = settings.long_body_lines,
        ):
            return max(existing, 1)
        case Context.CLASS if block.compact:
            return 0
        case Context.FUNCTION if (
            touches_definition
            or existing
            or isinstance(current, ast.Return)
        ):
            return existing
        case Context.FUNCTION:
            separated = (
                assignment_break
                or commented
                or is_guard(statement = previous)
                or is_multiline(statement = previous)
                and is_multiline(statement = current)
            )
            return 1 if separated else 0
        case _:
            return existing


def build_case_gap_edits(*, match: ast.Match, lines: list[str]) -> list[GapEdit]:
    """No blank lines between the cases of a match."""
    edits: list[GapEdit] = []
    for previous, current in itertools.pairwise(match.cases):
        previous_end = find_last_row(node = previous.body[-1])

        # only blank lines and comments sit between a case body and the next `case`
        header_row = next(
            row
            for row in range(previous_end + 1, current.pattern.lineno + 1)
            if lines[row - 1].strip() and not lines[row - 1].lstrip().startswith("#")
        )

        start_row = find_leading_row(
            row = header_row,
            floor_row = previous_end,
            lines = lines,
        )
        gap_rows = range(previous_end + 1, start_row)
        if gap_rows and not any(lines[row - 1].strip() for row in gap_rows):
            edits.append(
                GapEdit(
                    start_index = previous_end,
                    end_index = start_row - 1,
                    blank_lines = 0,
                )
            )

    return edits


def has_long_body(*, statement: ast.stmt, most_lines: int) -> bool:
    """A for, while or if whose body, with any else part, spans more than most_lines."""
    return (
        isinstance(statement, LOOPS_AND_BRANCHES)
        and find_last_row(node = statement) - statement.body[0].lineno + 1 > most_lines
    )


def is_simple_assignment(*, statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Assign | ast.AnnAssign | ast.AugAssign)
        and not is_multiline(statement = statement)
    )


def is_guard(*, statement: ast.stmt) -> bool:
    """An if without else that ends by leaving: return, raise, continue or break."""
    return (
        isinstance(statement, ast.If)
        and not statement.orelse
        and isinstance(
            statement.body[-1],
            ast.Return | ast.Raise | ast.Continue | ast.Break,
        )
    )


def is_multiline(*, statement: ast.stmt) -> bool:
    return find_last_row(node = statement) > find_first_row(statement = statement)
