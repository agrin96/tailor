import io
import re
import ast
import builtins
from collections import Counter
from dataclasses import dataclass
from collections.abc import Iterator

from tailor.config import PythonVersion
from tailor.constants import MemberKind, LOGGER_NAME, LAZY_ANNOTATIONS_PYTHON
from tailor.syntax import (
    is_docstring,
    find_last_row,
    find_first_row,
    classify_member,
    find_body_end_row,
)


@dataclass(frozen = True)
class AliasBlock:
    """dependencies: the definitions that move above the aliases, in written order.
    aliases: the `type` statements that move, in written order."""
    dependencies: list[ast.stmt]
    aliases: list[ast.TypeAlias]


@dataclass(frozen = True)
class AliasCycle:
    """alias: the alias whose value needs the name. name: the name that cannot move above
    the aliases."""
    alias: str
    name: str


@dataclass(frozen = True)
class OrderedAliases:
    """source: the module after the pass. warning: why the aliases kept their place, or
    None."""
    source: str
    warning: str | None = None


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
                    reorder_class(
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


def reorder_class(
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
    groups: dict[
        tuple[MemberKind, str], list[ast.FunctionDef | ast.AsyncFunctionDef]
    ] = {}

    # all definitions of one name (overloads, a property's setter) move as one group
    for member in members:
        kind = classify_member(member = member)
        if (
            kind is not None
            and isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
        ):
            groups.setdefault((kind, member.name), []).append(member)

    ordered_groups = sorted(
        groups.items(),
        key = lambda item: rank_member_group(
            kind = item[0][0],
            name = item[0][1],
            group = item[1],
            lines = lines,
            constructors = constructors,
        ),
    )
    moved = [member for _, group in ordered_groups for member in group]
    rest = [member for member in members if classify_member(member = member) is None]
    ordered = moved + rest

    if ordered == members:
        return None

    floor_rows = (
        [
            find_last_row(node = body[members_start - 1])
            if members_start
            else node.lineno
        ]
        + [find_last_row(node = member) for member in members[:-1]]
    )

    start_rows = [
        find_member_start_row(member = member, floor_row = floor_row, lines = lines)
        for member, floor_row in zip(members, floor_rows)
    ]

    last_row = find_body_end_row(member = members[-1], lines = lines)
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


def rank_member_group(
    *,
    kind: MemberKind,
    name: str,
    group: list[ast.FunctionDef | ast.AsyncFunctionDef],
    lines: list[str],
    constructors: tuple[str, ...],
) -> tuple[int, int, int]:
    """Dunders, then classmethods, then properties. The constructors lead the dunders in
    written order; every other group follows shortest body first."""
    if kind == MemberKind.DUNDER and name in constructors:
        return kind, 0, 0

    # blank lines do not count, so the spacing pass cannot change the order
    length = sum(
        1
        for member in group
        for row in range(member.body[0].lineno, find_last_row(node = member) + 1)
        if lines[row - 1].strip()
    )
    return kind, 1, length


def order_type_aliases(*, source: str, target: PythonVersion | None) -> OrderedAliases:
    """Move the top-level `type` statements up, in written order, to right after the imports
    and the logger, with the definitions their values need above them. The alias's value is
    evaluated lazily, but its name is bound where the statement stands: an alias stays when
    a statement it would move past uses that name. When a needed definition cannot move, the
    aliases keep their place and the result has a warning."""
    body = ast.parse(source).body
    plan = plan_alias_block(body = body, target = target)

    match plan:
        case AliasCycle():
            return OrderedAliases(
                source = source,
                warning = (
                    f"type alias {plan.alias} needs {plan.name}, which cannot move above"
                    " the aliases; the aliases keep their place"
                ),
            )
        case _:
            return OrderedAliases(
                source = move_alias_block(source = source, body = body, block = plan),
            )


def plan_alias_block(
    *,
    body: list[ast.stmt],
    target: PythonVersion | None,
) -> AliasBlock | AliasCycle:
    """The aliases that move up and the definitions that move above them. An alias blocks on
    the statements it would move past, except the definitions that move up too; the aliases
    and the definitions they need are found again until neither changes."""
    anchor = count_leading_statements(body = body)
    has_future_annotations = any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in body
    )

    has_lazy_annotations = (
        (target is not None and target >= LAZY_ANNOTATIONS_PYTHON)
        or has_future_annotations
    )

    dependencies: list[ast.stmt] = []
    while True:
        aliases = [
            statement
            for index, statement in enumerate(body)
            if isinstance(statement, ast.TypeAlias)
            # a wildcard import can bind any name, so no alias moves past one
            and not any(
                {statement.name.id, "*"} & list_bound_or_used_names(statement = crossed)
                for crossed in body[anchor:index]
                if crossed not in dependencies
            )
        ]

        found = find_alias_dependencies(
            body = body,
            anchor = anchor,
            aliases = aliases,
            has_lazy_annotations = has_lazy_annotations,
        )

        match found:
            case AliasCycle():
                return found
            case _ if found == dependencies:
                return AliasBlock(dependencies = dependencies, aliases = aliases)
            case _:
                dependencies = found


def find_alias_dependencies(
    *,
    body: list[ast.stmt],
    anchor: int,
    aliases: list[ast.TypeAlias],
    has_lazy_annotations: bool,
) -> list[ast.stmt] | AliasCycle:
    """The definitions the alias values need, in written order, followed through what each
    one evaluates when it runs. A needed name that is bound by the imports alone, or not at
    module level, needs nothing. Any other name must be bound once, by a definition that no
    wildcard import precedes, or it is a cycle. A definition of a name the module has before
    it runs, a builtin or a dunder, never moves: a read above it would see the new value."""
    bindings = Counter(
        name
        for statement in body
        for name in list_module_bindings(statement = statement)
    )

    imported = {
        name
        for statement in body[:anchor]
        for name in list_module_bindings(statement = statement)
    }

    definitions: dict[str, ast.stmt] = {}
    for statement in body[anchor:]:
        if "*" in list_module_bindings(statement = statement):
            break

        match read_definition_name(statement = statement):
            case str() as name if (
                bindings[name] == 1
                and name not in vars(builtins)
                and not (name.startswith("__") and name.endswith("__"))
            ):
                definitions[name] = statement
            case _:
                pass

    # an alias the block holds needs no move, whatever other aliases its value names
    alias_names = {
        statement.name.id
        for statement in body
        if isinstance(statement, ast.TypeAlias)
    }

    pending = [
        (alias.name.id, name)
        for alias in aliases
        for name in sorted(list_alias_value_names(value = alias.value) - alias_names)
    ]

    found: dict[str, ast.stmt] = {}
    while pending:
        alias, name = pending.pop()
        is_satisfied = (
            name in found
            or not bindings[name]
            or (name in imported and bindings[name] == 1)
        )

        if is_satisfied:
            continue

        if name not in definitions:
            return AliasCycle(alias = alias, name = name)

        found[name] = definitions[name]
        pending.extend(
            (alias, needed)
            for needed in sorted(
                list_evaluated_names(
                    statement = definitions[name],
                    has_lazy_annotations = has_lazy_annotations,
                )
            )
        )

    moved = set(map(id, found.values()))
    return [statement for statement in body if id(statement) in moved]


def list_alias_value_names(*, value: ast.expr) -> set[str]:
    """The names an alias value uses as values: every name in a call, callee and arguments,
    and every name in the metadata of an `Annotated`. Type positions do not count."""
    expressions: list[ast.expr] = []
    for node in ast.walk(value):
        match node:
            case ast.Call():
                expressions.append(node)
            case ast.Subscript(
                value = ast.Name(id = "Annotated") | ast.Attribute(attr = "Annotated"),
                slice = ast.Tuple(elts = [_, *metadata]),
            ):
                expressions.extend(metadata)
            case _:
                pass

    return {
        node.id
        for expression in expressions
        for node in ast.walk(expression)
        if isinstance(node, ast.Name)
    }


def list_evaluated_names(
    *,
    statement: ast.stmt,
    has_lazy_annotations: bool,
) -> set[str]:
    """The names a definition evaluates when it runs: a constant's value, a function's
    decorators and defaults, a class's decorators, bases, keywords and body. Function bodies
    never count; annotations count unless they are evaluated lazily. An assignment target
    counts for the names it reads, such as the `REGISTRY` of `REGISTRY["key"] = value`."""
    expressions: list[ast.AST] = []
    nested: list[ast.stmt] = []

    match statement:
        case ast.Assign(targets = targets, value = value):
            expressions.extend([*targets, value])
        case ast.AnnAssign(target = target, annotation = annotation, value = value):
            expressions.extend(
                [
                    target,
                    *([value] if value else []),
                    *([] if has_lazy_annotations else [annotation]),
                ]
            )
        case ast.FunctionDef(args = arguments) | ast.AsyncFunctionDef(args = arguments):
            expressions.extend(list_header_expressions(statement = statement))
            parameters = [
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
                *([arguments.vararg] if arguments.vararg else []),
                *([arguments.kwarg] if arguments.kwarg else []),
            ]

            annotations = [
                *(parameter.annotation for parameter in parameters),
                statement.returns,
            ]

            if not has_lazy_annotations:
                expressions.extend(
                    annotation
                    for annotation in annotations
                    if annotation
                )
        case ast.ClassDef():
            expressions.extend(list_header_expressions(statement = statement))
            nested.extend(statement.body)
        case _:
            expressions.append(statement)

    nodes = [
        node
        for expression in expressions
        for node in walk_evaluated_nodes(node = expression)
    ]

    # `LIMIT += 1` reads LIMIT, though its target is stored
    return (
        {
            node.id
            for node in nodes
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        | {
            node.target.id
            for node in nodes
            if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
        }
        | {
            name
            for member in nested
            for name in list_evaluated_names(
                statement = member,
                has_lazy_annotations = has_lazy_annotations,
            )
        }
    )


def list_header_expressions(
    *,
    statement: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> list[ast.expr]:
    """What a function or class evaluates where it is defined, except its annotations and a
    class's body: decorators and defaults, or decorators, bases and keywords."""
    match statement:
        case ast.ClassDef():
            return [
                *statement.decorator_list,
                *statement.bases,
                *(keyword.value for keyword in statement.keywords),
            ]
        case _:
            return [
                *statement.decorator_list,
                *statement.args.defaults,
                *(default for default in statement.args.kw_defaults if default),
            ]


def walk_evaluated_nodes(*, node: ast.AST) -> Iterator[ast.AST]:
    """Every node that runs when the node runs, as ast.walk gives them, except that a lambda's
    body runs only when it is called: of a lambda, only the defaults count."""
    pending = [node]
    while pending:
        current = pending.pop()
        yield current

        match current:
            case ast.Lambda(args = arguments):
                pending.extend(
                    [
                        *arguments.defaults,
                        *(default for default in arguments.kw_defaults if default),
                    ]
                )
            case _:
                pending.extend(ast.iter_child_nodes(current))


def list_module_bindings(*, statement: ast.stmt) -> list[str]:
    """The module-level names a top-level statement binds, once for each binding: a function
    or class binds its name, a `global` in it and an assignment expression in its header
    bind theirs; any other statement binds every name it stores, also in nested blocks. A
    wildcard import binds `*`."""
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            return [
                statement.name,
                *(
                    name
                    for node in ast.walk(statement)
                    if isinstance(node, ast.Global)
                    for name in node.names
                ),
                *(
                    node.id
                    for expression in list_header_expressions(statement = statement)
                    for node in walk_evaluated_nodes(node = expression)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                ),
            ]
        case _:
            pass

    names: list[str] = []
    for node in ast.walk(statement):
        match node:
            case (
                ast.Name(id = name, ctx = ast.Store())
                | ast.FunctionDef(name = name)
                | ast.AsyncFunctionDef(name = name)
                | ast.ClassDef(name = name)
                | ast.MatchAs(name = str() as name)
                | ast.MatchStar(name = str() as name)
                | ast.MatchMapping(rest = str() as name)
                | ast.ExceptHandler(name = str() as name)
            ):
                names.append(name)
            case ast.alias(name = name, asname = asname):
                names.append(asname or name.split(".")[0])
            case ast.Global(names = declared):
                names.extend(declared)
            case _:
                pass

    return names


def read_definition_name(*, statement: ast.stmt) -> str | None:
    """The name a movable definition binds: a function, a class, or a constant assigned to
    one name. None for any other statement."""
    match statement:
        case (
            ast.FunctionDef(name = name)
            | ast.AsyncFunctionDef(name = name)
            | ast.ClassDef(name = name)
            | ast.Assign(targets = [ast.Name(id = name)])
            | ast.AnnAssign(target = ast.Name(id = name), value = ast.expr())
        ):
            return name
        case _:
            return None


def move_alias_block(*, source: str, body: list[ast.stmt], block: AliasBlock) -> str:
    """The source with the dependencies and then the aliases right after the imports and the
    logger. Each moves with the comments above it and the deeper comments that end it."""
    anchor = count_leading_statements(body = body)
    moved = [*block.dependencies, *block.aliases]

    if not block.aliases or body[anchor : anchor + len(moved)] == moved:
        return source

    lines = io.StringIO(source).readlines()
    floor_rows = {
        id(statement): find_last_row(node = previous) if previous else 0
        for previous, statement in zip([None, *body], body)
    }

    moved_rows = {
        id(statement): range(
            find_member_start_row(
                member = statement,
                floor_row = floor_rows[id(statement)],
                lines = lines,
            ),
            find_body_end_row(member = statement, lines = lines) + 1,
        )
        for statement in moved
    }

    # a blank line ends the dependencies; the spacing pass sets the gaps between them
    dependency_lines = [
        *(
            lines[row - 1]
            for statement in block.dependencies
            for row in moved_rows[id(statement)]
        ),
        *(["\n"] if block.dependencies else []),
    ]

    alias_lines = [
        lines[row - 1]
        for alias in block.aliases
        for row in moved_rows[id(alias)]
    ]

    removed = {row for rows in moved_rows.values() for row in rows}

    for rows in moved_rows.values():
        row = rows[-1] + 1
        while row <= len(lines) and not lines[row - 1].strip():
            removed.add(row)
            row += 1

    anchor_row = (
        find_last_row(node = body[anchor - 1])
        if anchor
        else count_header_rows(lines = lines)
    )

    rest = [
        line
        for row, line in enumerate(lines, 1)
        if row > anchor_row and row not in removed
    ]

    while rest and not rest[0].strip():
        rest.pop(0)

    # blank lines left behind at the end of the file, where the last alias stood, go too
    return (
        "".join(
            [*lines[:anchor_row], "\n", *dependency_lines, *alias_lines, "\n", *rest],
        ).rstrip("\n")
        + "\n"
    )


def list_bound_or_used_names(*, statement: ast.stmt) -> set[str]:
    """Every name the statement binds or reads: names, definitions, imports, match patterns,
    and except clauses."""
    names: set[str] = set()
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
            case _:
                pass

    return names


def count_header_rows(*, lines: list[str]) -> int:
    """How many lines at the top of the file must stay first: a shebang, and an encoding
    declaration on one of the first two lines."""
    rows = 0
    for row, line in enumerate(lines[:2], 1):
        is_header = (
            (row == 1 and line.startswith("#!"))
            or re.match(r"[ \t\f]*#.*?coding[:=]", line) is not None
        )

        if is_header:
            rows = row

    return rows


def count_leading_statements(*, body: list[ast.stmt]) -> int:
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
        case _:
            return False


def find_member_start_row(*, member: ast.stmt, floor_row: int, lines: list[str]) -> int:
    """The first row of the member, moved up over the comments at its own indentation above
    it, also across blank lines. A deeper comment ends the body above, and any code line
    (a class header, the member above) stops the walk too."""
    start_row = find_first_row(statement = member)
    for row in range(find_first_row(statement = member) - 1, floor_row, -1):
        line = lines[row - 1]
        if not line.strip():
            continue

        is_own_comment = (
            line.lstrip().startswith("#")
            and len(line) - len(line.lstrip()) <= member.col_offset
        )

        if not is_own_comment:
            break

        start_row = row

    return start_row
