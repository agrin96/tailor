"""Inline SQL: a string whose first line is `--sql` is laid out at the column of its opening
quotes, by syntaqlite for SQLite and by sqruff for every other dialect."""

import io
import re
import tempfile
import tokenize
import functools
import itertools
import sysconfig
import subprocess
from pathlib import Path

import syntaqlite

from tailor.constants import (
    SQL_FORMAT,
    SQL_MARKER,
    SQRUFF_CONFIG,
    TRIPLE_QUOTES,
    MINIMUM_SQL_WIDTH,
    SQL_PARAMETER_NAME,
    DEFAULT_SQL_DIALECT,
    SQL_PARAMETER_PATTERN,
    CASE_SENSITIVE_NAME_DIALECTS,
)


def format_sql_strings(*, source: str, line_length: int, dialect: str) -> str:
    """Lay out the SQL of each `\"\"\"--sql` string. A string stays as written when its
    SQL cannot be parsed, when its text has a backslash, when a line break is part of a
    quoted SQL value, or when the layout would change anything but whitespace and case."""
    lines = io.StringIO(source).readlines()
    tokens = [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if is_sql_string(token = token)
        and is_safe_to_lay_out(sql = sql_text(token = token))
    ]

    if not tokens:
        return source

    queries = [sql_text(token = token) for token in tokens]
    masked = [masked_parameters(sql = query) for query in queries]
    widths = [max(line_length - token.start[1], MINIMUM_SQL_WIDTH) for token in tokens]

    laid_out = (
        syntaqlite_layout(queries = [sql for sql, _ in masked], widths = widths)
        if dialect == DEFAULT_SQL_DIALECT
        else sqruff_layout(
            queries = [sql for sql, _ in masked],
            width = min(widths),
            dialect = dialect,
        )
    )

    restored = [
        None if sql is None else restored_parameters(sql = sql, parameters = parameters)
        for sql, (_, parameters) in zip(laid_out, masked)
    ]

    edits = [
        (token, rewritten_token(token = token, sql = sql))
        for token, query, sql in zip(tokens, queries, restored)
        if sql is not None
        and sql_signature(sql = sql, dialect = dialect)
        == sql_signature(sql = query, dialect = dialect)
    ]

    for token, text in reversed(edits):
        (start_row, start_column), (end_row, end_column) = token.start, token.end
        lines[start_row - 1 : end_row] = [
            lines[start_row - 1][:start_column]
            + text
            + lines[end_row - 1][end_column:],
        ]

    return "".join(lines)


def syntaqlite_layout(*, queries: list[str], widths: list[int]) -> list[str | None]:
    """Each query laid out by syntaqlite, or None for one it cannot parse."""
    return [
        syntaqlite_query(sql = sql, width = width)
        for sql, width in zip(queries, widths)
    ]


def syntaqlite_query(*, sql: str, width: int) -> str | None:
    try:
        return sql_engine().format_sql(sql, line_width = width, **SQL_FORMAT).strip()
    except syntaqlite.FormatError:
        return None


def masked_parameters(*, sql: str) -> tuple[str, dict[str, str]]:
    """The SQL with each driver placeholder outside quotes and comments replaced by a plain
    name, and each name with the placeholder it stands for. No dialect parses every style
    (`%s` none of them), and a query a dialect cannot parse is not laid out."""
    if SQL_PARAMETER_NAME in sql:
        return sql, {}

    parameters: dict[str, str] = {}

    def stand_in(match: re.Match) -> str:
        name = f"{SQL_PARAMETER_NAME}{len(parameters)}"
        parameters[name] = match.group()
        return name

    runs = itertools.groupby(sql_engine().tokenize(sql), key = is_quoted_or_comment)
    masked = "".join(
        text if quoted else re.sub(SQL_PARAMETER_PATTERN, stand_in, text)
        for quoted, run in runs
        for text in ["".join(token["text"] for token in run)]
    )
    return masked, parameters


def restored_parameters(*, sql: str, parameters: dict[str, str]) -> str:
    return re.sub(
        rf"{SQL_PARAMETER_NAME}\d+",
        lambda match: parameters.get(match.group(), match.group()),
        sql,
    )


def is_quoted_or_comment(token: dict) -> bool:
    return (
        token["category"] in ("string", "comment")
        or token["text"][:1]
        in (
            '"',
            "`",
            "[",
        )
    )


def sqruff_layout(*, queries: list[str], width: int, dialect: str) -> list[str | None]:
    """All of a file's queries laid out by one sqruff run, or None for a query with a part
    sqruff cannot parse: it lays out the rest of such a query and leaves that part as is."""
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "tailor.sqruff"
        config.write_text(SQRUFF_CONFIG.format(dialect = dialect, width = width))
        paths = [Path(folder) / f"query_{index}.sql" for index in range(len(queries))]
        for path, sql in zip(paths, queries):
            path.write_text(sql.strip() + "\n")

        report = subprocess.run(
            [
                sqruff_binary(),
                "fix",
                "--parsing-errors",
                "--config",
                str(config),
                *(str(path) for path in paths),
            ],
            capture_output = True,
            text = True,
            check = False,
        ).stderr

        # the report has a `== [file] FAIL` header per file, and a `????` line per part
        # sqruff could not parse
        sections = re.split(r"^== \[", report, flags = re.MULTILINE)
        unparsable = {
            section.partition("]")[0]
            for section in sections
            if "| ???? |" in section
        }
        return [
            None if str(path) in unparsable else path.read_text().strip()
            for path in paths
        ]


@functools.cache
def sqruff_binary() -> str:
    """The sqruff program the sqruff package installs beside the Python that runs tailor."""
    return str(Path(sysconfig.get_path("scripts")) / "sqruff")


@functools.cache
def sql_engine() -> syntaqlite.Syntaqlite:
    """One syntaqlite engine for each process: it loads the SQLite grammar once."""
    return syntaqlite.Syntaqlite()


def rewritten_token(*, token: tokenize.TokenInfo, sql: str) -> str:
    """The string token with the SQL placed at the column of its opening quotes."""
    prefix_length = len(token.string) - len(token.string.lstrip("rRbBuU"))
    prefix = token.string[:prefix_length]
    quotes = token.string[prefix_length : prefix_length + 3]
    indent = " " * token.start[1]
    body = "".join(f"{indent}{line}\n" if line else "\n" for line in sql.split("\n"))
    return f"{prefix}{quotes}{SQL_MARKER}{body}{indent}{quotes}"


def sql_text(*, token: tokenize.TokenInfo) -> str:
    """The SQL between the marker line and the closing quotes."""
    prefix_length = len(token.string) - len(token.string.lstrip("rRbBuU"))
    return token.string[prefix_length + 3 + len(SQL_MARKER) : -3]


def is_safe_to_lay_out(*, sql: str) -> bool:
    """A backslash is an escape whose meaning a new layout could change."""
    return "\\" not in sql and not has_significant_line_break(sql = sql)


def has_significant_line_break(*, sql: str) -> bool:
    """True when a line break is part of what the SQL means: inside a quoted value or name,
    or between two quoted values, which some databases join into one value."""
    previous_is_string = False
    break_since_previous = False

    for token in sql_engine().tokenize(sql):
        text = token["text"]
        if text.isspace():
            break_since_previous = break_since_previous or "\n" in text
            continue

        is_string = token["category"] == "string"
        if "\n" in text and token["category"] in ("string", "identifier"):
            return True

        if is_string and previous_is_string and break_since_previous:
            return True

        previous_is_string = is_string
        break_since_previous = False

    return False


def sql_signature(*, sql: str, dialect: str) -> tuple[tuple[str, str], ...]:
    """What the SQL means: its tokens without whitespace, keywords in upper case, comments
    with their inner whitespace collapsed. Quoted values count exactly. In a dialect whose
    unquoted names ignore case, those names count in upper case too, because a word that
    SQLite reads as a name can be a keyword elsewhere (`ILIKE`)."""
    fold_names = dialect not in CASE_SENSITIVE_NAME_DIALECTS
    return tuple(
        (
            token["category"],
            signature_text(
                category = token["category"],
                text = token["text"],
                fold_names = fold_names,
            ),
        )
        for token in sql_engine().tokenize(sql)
        if not token["text"].isspace()
    )


def signature_text(*, category: str, text: str, fold_names: bool) -> str:
    quoted = text[:1] in ('"', "`", "[")
    match category:
        case "keyword":
            return text.upper()
        case "comment":
            return " ".join(text.split())
        case "identifier" if fold_names and not quoted:
            return text.upper()
    return text


def is_sql_string(*, token: tokenize.TokenInfo) -> bool:
    """A triple-quoted string whose text starts with the SQL marker, right after the quotes."""
    body = token.string.lstrip("rRbBuU")
    return (
        token.type == tokenize.STRING
        and body.startswith(TRIPLE_QUOTES)
        and body[3:].startswith(SQL_MARKER)
    )
