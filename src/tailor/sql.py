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
from typing import TYPE_CHECKING
from dataclasses import dataclass

import syntaqlite

from tailor.constants import (
    SQL_MARKER,
    SqlDialect,
    SQRUFF_CONFIG,
    TRIPLE_QUOTES,
    SqlTokenCategory,
    MINIMUM_SQL_WIDTH,
    SQL_PARAMETER_NAME,
    SQL_PARAMETER_PATTERN,
    STRING_PREFIX_LETTERS,
    QUOTED_SQL_NAME_STARTS,
    SQL_DOLLAR_QUOTE_PATTERN,
)


if TYPE_CHECKING:
    from syntaqlite import Token


@dataclass(frozen = True)
class MaskedQuery:
    """sql: the query with each driver placeholder replaced by a plain name.
    parameters: each of those names, with the placeholder it stands for."""
    sql: str
    parameters: dict[str, str]


def format_sql_strings(
    *,
    source: str,
    line_length: int,
    dialect: SqlDialect,
    indent_width: int,
) -> str:
    """Lay out the SQL of each `\"\"\"--sql` string. A string stays as written when its
    SQL cannot be parsed, when its text has a backslash, when a line break is part of a
    quoted SQL value, or when the layout would change anything but whitespace and case."""
    lines = io.StringIO(source).readlines()
    strings = [
        token
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if is_sql_string(token = token)
    ]

    texts = [
        token.string.lstrip(STRING_PREFIX_LETTERS)[3 + len(SQL_MARKER) : -3]
        for token in strings
    ]
    safe = [is_safe_to_lay_out(sql = text) for text in texts]
    tokens = list(itertools.compress(strings, safe))
    queries = list(itertools.compress(texts, safe))

    if not tokens:
        return source

    widths = [max(line_length - token.start[1], MINIMUM_SQL_WIDTH) for token in tokens]
    laid_out = (
        [
            lay_out_with_syntaqlite(
                sql = query,
                width = width,
                indent_width = indent_width,
            )
            for query, width in zip(queries, widths)
        ]
        if dialect == SqlDialect.SQLITE
        else lay_out_with_sqruff(
            queries = queries,
            width = min(widths),
            dialect = dialect,
            indent_width = indent_width,
        )
    )

    # bottom up, so an edit never moves the rows of a string still ahead
    for token, query, sql in reversed(list(zip(tokens, queries, laid_out))):
        if sql is None:
            continue

        is_same_sql = build_sql_signature(
            sql = sql,
            dialect = dialect,
        ) == build_sql_signature(sql = query, dialect = dialect)

        if not is_same_sql:
            continue

        (start_row, start_column), (end_row, end_column) = token.start, token.end
        lines[start_row - 1 : end_row] = [
            lines[start_row - 1][:start_column]
            + rewrite_token(token = token, sql = sql)
            + lines[end_row - 1][end_column:]
        ]

    return "".join(lines)


def lay_out_with_syntaqlite(*, sql: str, width: int, indent_width: int) -> str | None:
    """The query laid out by syntaqlite, or None when syntaqlite cannot parse it. SQL that
    ends with `;` keeps one after each statement; other SQL gets none, since it can be a
    piece of a longer query. Comments after the last statement do not count."""
    code_tokens = [
        token
        for token in load_sql_engine().tokenize(sql)
        if not token["text"].isspace() and token["category"] != SqlTokenCategory.COMMENT
    ]

    ends_with_semicolon = bool(code_tokens) and code_tokens[-1]["text"] == ";"

    try:
        return (
            load_sql_engine()
            .format_sql(
                sql,
                line_width = width,
                indent_width = indent_width,
                keyword_case = "upper",
                semicolons = ends_with_semicolon,
            )
            .strip()
        )
    except syntaqlite.FormatError:
        return None


def mask_parameters(*, sql: str) -> MaskedQuery:
    """Replace each driver placeholder outside quotes and comments with a plain name. No
    sqruff dialect parses every style (`%s` none of them), and a query sqruff cannot parse
    is not laid out."""
    if SQL_PARAMETER_NAME in sql:
        return MaskedQuery(sql = sql, parameters = {})

    runs = [
        (quoted, "".join(token["text"] for token in run))
        for quoted, run in itertools.groupby(
            load_sql_engine().tokenize(sql),
            key = lambda token: (
                token["category"] in (SqlTokenCategory.STRING, SqlTokenCategory.COMMENT)
                or token["text"].startswith(QUOTED_SQL_NAME_STARTS)
            ),
        )
    ]

    placeholders = [
        match.group()
        for quoted, text in runs
        if not quoted
        for match in re.finditer(SQL_PARAMETER_PATTERN, text)
    ]

    # both passes meet the placeholders in the same order, so the numbers line up
    numbers = itertools.count()
    masked = "".join(
        (
            text
            if quoted
            else re.sub(
                SQL_PARAMETER_PATTERN,
                lambda _: f"{SQL_PARAMETER_NAME}{next(numbers)}",
                text,
            )
        )
        for quoted, text in runs
    )

    return MaskedQuery(
        sql = masked,
        parameters = {
            f"{SQL_PARAMETER_NAME}{number}": placeholder
            for number, placeholder in enumerate(placeholders)
        },
    )


def lay_out_with_sqruff(
    *,
    queries: list[str],
    width: int,
    dialect: SqlDialect,
    indent_width: int,
) -> list[str | None]:
    """All of a file's queries laid out by one sqruff run, or None for a query with a part
    sqruff cannot parse: it lays out the rest of such a query and leaves that part as is."""
    masked = [mask_parameters(sql = query) for query in queries]
    with tempfile.TemporaryDirectory() as folder:
        config = Path(folder) / "tailor.sqruff"
        config.write_text(
            SQRUFF_CONFIG.format(
                dialect = dialect,
                width = width,
                indent_width = indent_width,
            )
        )

        paths = [Path(folder) / f"query_{index}.sql" for index in range(len(queries))]
        for path, query in zip(paths, masked):
            path.write_text(query.sql.strip() + "\n")

        report = subprocess.run(
            [
                str(Path(sysconfig.get_path("scripts")) / "sqruff"),
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
            (
                None
                if str(path) in unparsable
                else re.sub(
                    rf"{SQL_PARAMETER_NAME}\d+",
                    lambda match: query.parameters.get(match.group(), match.group()),
                    path.read_text().strip(),
                )
            )
            for path, query in zip(paths, masked)
        ]


@functools.cache
def load_sql_engine() -> syntaqlite.Syntaqlite:
    """One syntaqlite engine for each process: it loads the SQLite grammar once."""
    return syntaqlite.Syntaqlite()


def rewrite_token(*, token: tokenize.TokenInfo, sql: str) -> str:
    """The string token with the SQL placed at the column of its opening quotes."""
    prefix_length = len(token.string) - len(token.string.lstrip(STRING_PREFIX_LETTERS))
    prefix = token.string[:prefix_length]
    quotes = token.string[prefix_length : prefix_length + 3]
    indent = " " * token.start[1]
    body = "".join(f"{indent}{line}\n" if line else "\n" for line in sql.split("\n"))
    return f"{prefix}{quotes}{SQL_MARKER}{body}{indent}{quotes}"


def is_safe_to_lay_out(*, sql: str) -> bool:
    """A backslash is an escape whose meaning a new layout could change. The SQLite
    tokenizer does not read a dollar-quoted value as one, so it cannot see a line break
    inside it."""
    return (
        "\\" not in sql
        and not re.search(SQL_DOLLAR_QUOTE_PATTERN, sql)
        and not has_significant_line_break(sql = sql)
    )


def has_significant_line_break(*, sql: str) -> bool:
    """True when a line break is part of what the SQL means: inside a quoted value or name,
    or between two quoted values, which some databases join into one value."""
    previous_is_string = False
    break_since_previous = False

    for token in load_sql_engine().tokenize(sql):
        text = token["text"]
        if text.isspace():
            break_since_previous = break_since_previous or "\n" in text
            continue

        is_string = token["category"] == SqlTokenCategory.STRING
        is_quoted = token["category"] in (
            SqlTokenCategory.STRING,
            SqlTokenCategory.IDENTIFIER,
        )

        if "\n" in text and is_quoted:
            return True

        if is_string and previous_is_string and break_since_previous:
            return True

        previous_is_string = is_string
        break_since_previous = False

    return False


def build_sql_signature(
    *,
    sql: str,
    dialect: SqlDialect,
) -> tuple[tuple[str, str], ...]:
    """What the SQL means: its tokens without whitespace, keywords in upper case, comments
    with their inner whitespace collapsed. Quoted values count exactly. In a dialect whose
    unquoted names ignore case, those names count in upper case too, because a word that
    SQLite reads as a name can be a keyword elsewhere (`ILIKE`)."""
    fold_names = dialect not in SqlDialect.CASE_SENSITIVE_NAMES
    return tuple(
        (
            token["category"],
            normalize_signature_text(token = token, fold_names = fold_names),
        )
        for token in load_sql_engine().tokenize(sql)
        if not token["text"].isspace()
    )


def normalize_signature_text(*, token: Token, fold_names: bool) -> str:
    text = token["text"]
    is_unquoted_name = (
        token["category"] == SqlTokenCategory.IDENTIFIER
        and not text.startswith(QUOTED_SQL_NAME_STARTS)
    )

    match token["category"]:
        case SqlTokenCategory.KEYWORD:
            return text.upper()
        case SqlTokenCategory.COMMENT:
            return " ".join(text.split())
        case _ if fold_names and is_unquoted_name:
            return text.upper()
        case _:
            return text


def is_sql_string(*, token: tokenize.TokenInfo) -> bool:
    """A triple-quoted string whose text starts with the SQL marker, right after the quotes."""
    body = token.string.lstrip(STRING_PREFIX_LETTERS)
    return (
        token.type == tokenize.STRING
        and body.startswith(TRIPLE_QUOTES)
        and body[3:].startswith(SQL_MARKER)
    )
