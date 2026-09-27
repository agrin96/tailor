import io
import ast
import itertools
from enum import StrEnum
from dataclasses import dataclass
from collections.abc import Iterator

from tailor.config import Settings
from tailor.constants import DEFINITIONS, LOOPS_AND_BRANCHES
from tailor.syntax import (
    first_row,
    leading_row,
    body_end_row,
    is_docstring,
    is_short_dunder,
)


class Context(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    OTHER = "other"


@dataclass(frozen = True)
class Block:
    body: list[ast.stmt]
    context: Context
    compact: bool


@dataclass(frozen = True)
class GapEdit:
    start_index: int
    end_index: int
    blank_lines: int


def space_statements(*, source: str, settings: Settings) -> str:
    """Set the blank lines between sibling statements."""
    lines = io.StringIO(source).readlines()
    all_blocks = list(
        blocks(
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
        for edit in gap_edits(block = block, lines = lines, settings = settings)
    ]

    case_edits = [
        edit
        for block in all_blocks
        for statement in block.body
        if isinstance(statement, ast.Match)
        for edit in case_gap_edits(match = statement, lines = lines)
    ]

    for edit in sorted(
        edits + case_edits,
        key = lambda edit: edit.start_index,
        reverse = True,
    ):
        lines[edit.start_index : edit.end_index] = ["\n"] * edit.blank_lines
    return "".join(lines)


def blocks(
    *,
    body: list[ast.stmt],
    context: Context,
    compact: bool,
    lines: list[str],
    settings: Settings,
) -> Iterator[Block]:
    yield Block(body = body, context = context, compact = compact)
    for statement in body:
        child_context = child_context_of(statement = statement, context = context)
        compact_child = (
            isinstance(statement, ast.ClassDef)
            and is_compact(statement = statement, lines = lines, settings = settings)
        )

        for child_body in child_bodies(statement = statement):
            yield from blocks(
                body = child_body,
                context = child_context,
                compact = compact_child,
                lines = lines,
                settings = settings,
            )


def child_context_of(*, statement: ast.stmt, context: Context) -> Context:
    if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
        return Context.FUNCTION

    if isinstance(statement, ast.ClassDef):
        return Context.CLASS

    if context == Context.FUNCTION:
        return Context.FUNCTION
    return Context.OTHER


def child_bodies(*, statement: ast.stmt) -> Iterator[list[ast.stmt]]:
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
    rows = range(first_row(statement = statement), statement.end_lineno + 1)
    return (
        sum(1 for row in rows if lines[row - 1].strip()) <= settings.compact_class_lines
    )


def gap_edits(*, block: Block, lines: list[str], settings: Settings) -> list[GapEdit]:
    body = block.body
    if block.context == Context.FUNCTION and is_docstring(statement = body[0]):
        body = body[1:]
    pairs = list(itertools.pairwise(body))

    # comments indented deeper than a statement end its body, so they count as part of it
    end_rows = [body_end_row(member = previous, lines = lines) for previous, _ in pairs]

    start_rows = [
        leading_row(
            row = first_row(statement = current),
            floor_row = end_row,
            lines = lines,
        )
        for (_, current), end_row in zip(pairs, end_rows)
    ]

    gap_rows = [
        range(end_row + 1, start_row)
        for end_row, start_row in zip(end_rows, start_rows)
    ]

    breaks = assignment_breaks(
        body = body,
        gap_rows = gap_rows,
        group_size = settings.assignment_group_size,
    )
    edits = []

    for index, (previous, current) in enumerate(pairs):
        rows = gap_rows[index]

        # a comment between blank lines belongs to neither statement: keep the gap
        if any(lines[row - 1].strip() for row in rows):
            continue

        wanted = wanted_blank_lines(
            block = block,
            previous = previous,
            current = current,
            existing = len(rows),
            assignment_break = index in breaks,
            commented = start_rows[index] < first_row(statement = current),
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


def assignment_breaks(
    *,
    body: list[ast.stmt],
    gap_rows: list[range],
    group_size: int,
) -> set[int]:
    """Gaps that get a blank line: between the groups of a long assignment run, and after
    a run of two or more. Gap i sits between body[i] and body[i + 1]."""
    runs = []
    for index, statement in enumerate(body):
        if not is_simple_assignment(statement = statement):
            continue

        if runs and runs[-1][-1] == index - 1 and not gap_rows[index - 1]:
            runs[-1].append(index)
        else:
            runs.append([index])

    breaks = set()
    for run in runs:
        group_ends = list(
            itertools.accumulate(
                group_sizes(count = len(run), group_size = group_size),
            ),
        )
        breaks |= {run[0] + group_end - 1 for group_end in group_ends[:-1]}

        if len(run) >= 2 and run[-1] < len(body) - 1:
            breaks.add(run[-1])

    return breaks


def group_sizes(*, count: int, group_size: int) -> list[int]:
    """Below two groups' worth, one group. Then groups of group_size up to twice that less
    one, as even as possible: with 3, 5 stays one group and 7 is 4 + 3."""
    if count < 2 * group_size:
        return [count]

    groups = count // group_size
    size, larger = divmod(count, groups)
    return [size + 1] * larger + [size] * (groups - larger)


def wanted_blank_lines(
    *,
    block: Block,
    previous: ast.stmt,
    current: ast.stmt,
    existing: int,
    assignment_break: bool,
    commented: bool,
    short_dunders: bool,
    settings: Settings,
) -> int:
    touches_definition = (
        isinstance(previous, DEFINITIONS)
        or isinstance(current, DEFINITIONS)
    )

    match block.context:
        case Context.MODULE if touches_definition:
            return settings.module_definition_blank_lines
        case Context.MODULE if assignment_break:
            return max(existing, 1)
        case Context.CLASS if short_dunders:
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
    return existing


def case_gap_edits(*, match: ast.Match, lines: list[str]) -> list[GapEdit]:
    """No blank lines between the cases of a match."""
    edits = []
    for previous, current in itertools.pairwise(match.cases):
        previous_end = previous.body[-1].end_lineno

        # only blank lines and comments sit between a case body and the next `case`
        header_row = next(
            row
            for row in range(previous_end + 1, current.pattern.lineno + 1)
            if lines[row - 1].strip() and not lines[row - 1].lstrip().startswith("#")
        )

        start_row = leading_row(
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
        and statement.end_lineno - statement.body[0].lineno + 1 > most_lines
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
    return statement.end_lineno > first_row(statement = statement)
