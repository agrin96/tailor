import io
import ast

from tailor.syntax import first_row, body_end_row, content_length, is_dunder_definition


def order_class_members(*, source: str, constructors: tuple[str, ...]) -> str:
    """Move the dunder methods of each class up, after its leading attributes and before
    its first other method: constructors first, the other dunders shortest first. All
    definitions of one dunder (overloads) move together as one group, in written order.
    Other members keep their order. One class per parse, until no class changes."""
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
    groups: dict[str, list[ast.stmt]] = {}

    for member in members:
        if is_dunder_definition(statement = member):
            groups.setdefault(member.name, []).append(member)

    constructor_groups = [
        group
        for name, group in groups.items()
        if name in constructors
    ]

    other_groups = sorted(
        (group for name, group in groups.items() if name not in constructors),
        key = lambda group: sum(
            content_length(statement = dunder, lines = lines)
            for dunder in group
        ),
    )

    dunders = [
        dunder
        for group in constructor_groups + other_groups
        for dunder in group
    ]

    rest = [
        member
        for member in members
        if not is_dunder_definition(statement = member)
    ]
    ordered = dunders + rest
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
