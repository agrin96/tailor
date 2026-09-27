"""Tokens and brackets: what every layout pass reads before it edits a line."""

import io
import sys
import keyword
import tokenize
from dataclasses import field, dataclass

from tailor.constants import BracketText, SoftKeyword


# t-strings arrived in Python 3.14: before it, their tokens do not exist
if sys.version_info >= (3, 14):
    TEMPLATE_STARTS = (tokenize.FSTRING_START, tokenize.TSTRING_START)
    TEMPLATE_ENDS = (tokenize.FSTRING_END, tokenize.TSTRING_END)
else:
    TEMPLATE_STARTS = (tokenize.FSTRING_START,)
    TEMPLATE_ENDS = (tokenize.FSTRING_END,)

TEMPLATE_DEPTH_CHANGE = (
    {start: 1 for start in TEMPLATE_STARTS}
    | {end: -1 for end in TEMPLATE_ENDS}
)
VALUE_TYPES = (tokenize.NAME, tokenize.NUMBER, tokenize.STRING, *TEMPLATE_ENDS)


@dataclass(frozen = True)
class Bracket:
    """Children are the tokens directly inside: nested brackets show only their own
    opener and closer. The neighbor of a child is always a child, or the bracket itself."""

    before: tokenize.TokenInfo | None
    opener: tokenize.TokenInfo
    children: tuple[tokenize.TokenInfo, ...]
    closer: tokenize.TokenInfo
    in_template: bool


@dataclass(frozen = True)
class OpenBracket:
    """A bracket whose closer is still ahead. Its children collect as the scan goes on."""

    before: tokenize.TokenInfo | None
    opener: tokenize.TokenInfo
    in_template: bool
    children: list[tokenize.TokenInfo] = field(default_factory = list)


    def close(self, *, closer: tokenize.TokenInfo) -> Bracket:
        return Bracket(
            before = self.before,
            opener = self.opener,
            children = tuple(self.children),
            closer = closer,
            in_template = self.in_template,
        )


@dataclass(frozen = True)
class Edit:
    row: int
    start_column: int
    end_column: int
    text: str


def code_tokens(*, source: str) -> list[tokenize.TokenInfo]:
    """Every token except line breaks inside brackets and comments."""
    return [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in {tokenize.NL, tokenize.COMMENT}
    ]


def find_brackets(*, source: str) -> list[Bracket]:
    tokens = code_tokens(source = source)
    brackets = []
    open_brackets: list[OpenBracket] = []
    template_depth = 0

    for index, token in enumerate(tokens):
        if token.type == tokenize.OP and token.string in BracketText.CLOSERS:
            brackets.append(open_brackets.pop().close(closer = token))

        # the innermost open bracket owns every token inside it, nested openers and closers too
        if open_brackets:
            open_brackets[-1].children.append(token)

        template_depth += TEMPLATE_DEPTH_CHANGE.get(token.type, 0)
        if token.type == tokenize.OP and token.string in BracketText.OPENERS:
            open_brackets.append(
                OpenBracket(
                    before = tokens[index - 1] if index else None,
                    opener = token,
                    in_template = template_depth > 0,
                )
            )

    return brackets


def is_hugged(*, bracket: Bracket) -> bool:
    """Ruff's middle layout: brackets on their own lines, all content on one line between them."""
    if not bracket.children:
        return False

    first_row = bracket.children[0].start[0]
    return (
        bracket.opener.start[0]
        < first_row
        == bracket.children[-1].end[0]
        < bracket.closer.start[0]
    )


def is_comprehension(*, bracket: Bracket) -> bool:
    return any(is_code(token = child, string = "for") for child in bracket.children)


def is_explodable(*, bracket: Bracket) -> bool:
    """A comma separated list where a trailing comma is legal: call, definition, import, literal."""
    if (
        bracket.in_template
        or not bracket.children
        or is_code(token = bracket.children[-1], string = ",")
    ):
        return False

    if is_comprehension(bracket = bracket):
        return False

    if bracket.opener.string == "(":
        return (
            follows_value(bracket = bracket)
            or (
                bracket.before is not None
                and is_code(token = bracket.before, string = "import")
            )
        )

    if bracket.opener.string == "[":
        return not follows_value(bracket = bracket)
    return True


def follows_value(*, bracket: Bracket) -> bool:
    """A call or a subscript: the bracket comes right after a name, a literal or a closer."""
    before = bracket.before
    return (
        before is not None
        and (
            before.type == tokenize.OP
            and before.string in BracketText.CLOSERS
            or before.type in VALUE_TYPES
            and not keyword.iskeyword(before.string)
            and before.string not in SoftKeyword
        )
    )


def ends_value(*, token: tokenize.TokenInfo) -> bool:
    """A name, literal or closing bracket: what can stand right before a binary operator."""
    if token.type == tokenize.OP:
        return token.string in BracketText.CLOSERS
    return (
        token.type in VALUE_TYPES
        and (
            not keyword.iskeyword(token.string)
            or token.string in {"None", "True", "False"}
        )
    )


def ends_in_comment(*, bracket: Bracket, lines: list[str]) -> bool:
    """A comment after the packed line is often why ruff kept it apart: splitting the line
    would leave the comment on the last piece only."""
    last = bracket.children[-1]
    return lines[last.end[0] - 1][last.end[1] :].lstrip().startswith("#")


def comprehension_clause_indexes(
    *,
    children: tuple[tokenize.TokenInfo, ...],
) -> list[int]:
    """The children that start each for or if clause of a comprehension."""
    for_indexes = [
        index
        for index, child in enumerate(children)
        if is_code(token = child, string = "for")
    ]

    # an `async for` clause starts at its `async`
    async_indexes = [
        index - 1
        for index in for_indexes
        if is_code(token = children[index - 1], string = "async")
    ]

    plain_for_indexes = [
        index
        for index in for_indexes
        if index - 1 not in async_indexes
    ]

    if_indexes = [
        index
        for index, child in enumerate(children)
        if is_code(token = child, string = "if") and index > for_indexes[0]
    ]
    return async_indexes + plain_for_indexes + if_indexes


def apply_edits(*, source: str, edits: list[Edit]) -> str:
    lines = io.StringIO(source).readlines()
    for edit in sorted(
        edits,
        key = lambda edit: (edit.row, edit.start_column),
        reverse = True,
    ):
        line = lines[edit.row - 1]
        lines[edit.row - 1] = (
            line[: edit.start_column]
            + edit.text
            + line[edit.end_column :]
        )

    return "".join(lines)


def is_code(*, token: tokenize.TokenInfo, string: str) -> bool:
    """True for an operator or name token, never for text inside an f-string or t-string."""
    return token.type in {tokenize.OP, tokenize.NAME} and token.string == string
