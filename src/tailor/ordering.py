import io
import re
import ast

from tailor.constants import MemberKind, LOGGER_NAME
from tailor.syntax import (
    first_row,
    member_kind,
    body_end_row,
    is_docstring,
    content_length,
)


def order_class_members(*, source: str, constructors: tuple[str, ...]) -> str:
    """Move the dunders, classmethods and properties of each class up, after its leading
    attributes and before its first other method, in that order: constructors first, then
    each group shortest first. All definitions of one name (overloads, a property's setter)
    move together, in written order. Other members keep their order. One class per parse,
    until no class changes."""
    while True:
        lines = io.StringIO(source).readlines()

        reordered = next(
            (
                text
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.ClassDef)
                for text in [
                    reordered_class(
                        node = node,
                        lines = lines,
                        constructors = constructors,
                    )
                ]
                if text is not None
            ),
            None,
        )

        if reordered is None:
            return source

        source = reordered


def reordered_class(
    *,
    node: ast.ClassDef,
    lines: list[str],
    constructors: tuple[str, ...],
) -> str | None:
    """The source with this class's members in order, or None when it is in order already."""
    body = node.body
    members_start = next(
        (
            index
            for index, statement in enumerate(body)
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
        ),
        None,
    )

    if members_start is None:
        return None

    members = body[members_start:]
    groups: dict[tuple[MemberKind, str], list[ast.stmt]] = {}

    # all definitions of one name (overloads, a property's setter) move as one group
    for member in members:
        kind = member_kind(member = member)
        if kind is not None:
            groups.setdefault((kind, member.name), []).append(member)

    ordered_groups = sorted(
        groups.items(),
        key = lambda item: group_order(
            kind = item[0][0],
            name = item[0][1],
            group = item[1],
            lines = lines,
            constructors = constructors,
        ),
    )
    moved = [member for _, group in ordered_groups for member in group]
    rest = [member for member in members if member_kind(member = member) is None]
    ordered = moved + rest

    if ordered == members:
        return None

    floor_rows = (
        [body[members_start - 1].end_lineno if members_start else node.lineno]
        + [member.end_lineno for member in members[:-1]]
    )

    start_rows = [
        member_start_row(member = member, floor_row = floor_row, lines = lines)
        for member, floor_row in zip(members, floor_rows)
    ]

    last_row = body_end_row(member = members[-1], lines = lines)
    end_rows = [start_row - 1 for start_row in start_rows[1:]] + [last_row]

    chunks = {
        id(member): "".join(lines[start_row - 1 : end_row]).rstrip("\n") + "\n"
        for member, start_row, end_row in zip(members, start_rows, end_rows)
    }
    return (
        "".join(lines[: start_rows[0] - 1])
        + "\n".join(chunks[id(member)] for member in ordered)
        + "".join(lines[last_row:])
    )


def group_order(
    *,
    kind: MemberKind,
    name: str,
    group: list[ast.stmt],
    lines: list[str],
    constructors: tuple[str, ...],
) -> tuple[int, int, int]:
    """Dunders, then classmethods, then properties. The constructors lead the dunders in
    written order; every other group follows shortest body first."""
    if kind == MemberKind.DUNDER and name in constructors:
        return kind, 0, 0

    return (
        kind,
        1,
        sum(content_length(statement = member, lines = lines) for member in group),
    )


def order_type_aliases(*, source: str) -> str:
    """Move the top-level `type` statements up, in written order, to right after the imports
    and the logger. The alias's value is evaluated lazily, but its name is bound where the
    statement stands: an alias stays when a statement it would move past uses that name."""
    body = ast.parse(source).body
    anchor = leading_statement_count(body = body)

    aliases = [
        statement
        for index, statement in enumerate(body)
        if isinstance(statement, ast.TypeAlias)
        # a wildcard import can bind any name, so no alias moves past one
        and not any(
            {statement.name.id, "*"} & bound_or_used_names(statement = crossed)
            for crossed in body[anchor:index]
        )
    ]

    if not aliases or body[anchor : anchor + len(aliases)] == aliases:
        return source

    lines = io.StringIO(source).readlines()
    floor_rows = {
        id(statement): previous.end_lineno if previous else 0
        for previous, statement in zip([None, *body], body)
    }

    alias_rows = [
        range(
            member_start_row(
                member = alias,
                floor_row = floor_rows[id(alias)],
                lines = lines,
            ),
            alias.end_lineno + 1,
        )
        for alias in aliases
    ]
    alias_lines = [lines[row - 1] for rows in alias_rows for row in rows]
    removed = {row for rows in alias_rows for row in rows}

    # the blank lines after a moved alias go with it
    for rows in alias_rows:
        row = rows[-1] + 1
        while row <= len(lines) and not lines[row - 1].strip():
            removed.add(row)
            row += 1

    anchor_row = body[anchor - 1].end_lineno if anchor else header_rows(lines = lines)
    rest = [
        line
        for row, line in enumerate(lines, 1)
        if row > anchor_row and row not in removed
    ]

    while rest and not rest[0].strip():
        rest.pop(0)

    # blank lines left behind at the end of the file, where the last alias stood, go too
    return (
        "".join([*lines[:anchor_row], "\n", *alias_lines, "\n", *rest]).rstrip("\n")
        + "\n"
    )


def bound_or_used_names(*, statement: ast.stmt) -> set[str]:
    """Every name the statement binds or reads: names, definitions, imports, match patterns,
    and except clauses."""
    names = set()
    for node in ast.walk(statement):
        match node:
            case (
                ast.Name(id = name)
                | ast.FunctionDef(name = name)
                | ast.AsyncFunctionDef(name = name)
                | ast.ClassDef(name = name)
            ):
                names.add(name)
            case ast.alias(name = name, asname = asname):
                names.add(asname or name.split(".")[0])
            case (
                ast.MatchAs(name = str() as name)
                | ast.MatchStar(name = str() as name)
                | ast.MatchMapping(rest = str() as name)
                | ast.ExceptHandler(name = str() as name)
            ):
                names.add(name)

    return names


def header_rows(*, lines: list[str]) -> int:
    """How many lines at the top of the file must stay first: a shebang, and an encoding
    declaration on one of the first two lines."""
    rows = 0
    for row, line in enumerate(lines[:2], 1):
        if (
            (row == 1 and line.startswith("#!"))
            or re.match(r"[ \t\f]*#.*?coding[:=]", line)
        ):
            rows = row

    return rows


def leading_statement_count(*, body: list[ast.stmt]) -> int:
    """How many statements open the module: the docstring, the imports, then the logger."""
    count = 1 if body and is_docstring(statement = body[0]) else 0
    while count < len(body) and isinstance(body[count], ast.Import | ast.ImportFrom):
        count += 1

    while count < len(body) and is_logger(statement = body[count]):
        count += 1

    return count


def is_logger(*, statement: ast.stmt) -> bool:
    match statement:
        case (
            ast.Assign(targets = [ast.Name(id = name)])
            | ast.AnnAssign(target = ast.Name(id = name))
        ):
            return name == LOGGER_NAME
    return False


def member_start_row(*, member: ast.stmt, floor_row: int, lines: list[str]) -> int:
    """The first row of the member, moved up over the comments at its own indentation above
    it, also across blank lines. A deeper comment ends the body above, and any code line
    (a class header, the member above) stops the walk too."""
    start_row = first_row(statement = member)
    for row in range(first_row(statement = member) - 1, floor_row, -1):
        line = lines[row - 1]
        if not line.strip():
            continue

        if (
            not line.lstrip().startswith("#")
            or len(line) - len(line.lstrip()) > member.col_offset
        ):
            break

        start_row = row

    return start_row
