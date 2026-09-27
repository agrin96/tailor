"""Fixed values the passes share. Settings a project can change are in config.Settings."""

import ast
from enum import IntEnum, StrEnum, nonmember


class Marker(StrEnum):
    """Text the formatter adds while it works, and removes again. The split marker is built
    from two parts and the widener is written as escapes, so this file contains neither."""
    # an extra operand that makes ruff put one operand per line
    SPLIT = "__tailor_" + "split__"
    # added to each keyword name while ruff runs, so `name=` is as wide as `name = `
    WIDENER = "\u01c2\u01c2"


class BracketText(StrEnum):
    OPEN_ROUND = "("
    OPEN_SQUARE = "["
    OPEN_CURLY = "{"
    CLOSE_ROUND = ")"
    CLOSE_SQUARE = "]"
    CLOSE_CURLY = "}"
    OPENERS = nonmember((OPEN_ROUND, OPEN_SQUARE, OPEN_CURLY))
    CLOSERS = nonmember((CLOSE_ROUND, CLOSE_SQUARE, CLOSE_CURLY))


class MemberKind(IntEnum):
    """The class members that move up and group together, in their order in the class."""
    DUNDER = 0
    CLASSMETHOD = 1
    PROPERTY = 2


class SoftKeyword(StrEnum):
    """Names that are keywords only where a statement starts."""
    MATCH = "match"
    CASE = "case"


# ruff runs after the first; ordinary files settle in two or three
MAXIMUM_RUFF_PASSES = 10

# loosest first: a wrapped expression splits at the loosest operator in it
OPERATOR_TIERS = (
    {ast.BitOr: "|"},
    {ast.BitXor: "^"},
    {ast.BitAnd: "&"},
    {ast.LShift: "<<", ast.RShift: ">>"},
    {ast.Add: "+", ast.Sub: "-"},
    {ast.Mult: "*", ast.MatMult: "@", ast.Div: "/", ast.FloorDiv: "//", ast.Mod: "%"},
    {ast.Pow: "**"},
)

# a split inside a comparison or a `not` would read as the wrong grouping
COMPARISONS = ("==", "!=", "<", ">", "<=", ">=", "in", "is", "not")

# at the top level of a parenthesized expression these bind looser than a conditional
LOOSER_THAN_CONDITIONAL = (",", ":=", "lambda", "yield")

DEFINITIONS = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
IMPORTS = ast.Import | ast.ImportFrom
LOOPS_AND_BRANCHES = ast.For | ast.AsyncFor | ast.While | ast.If

# click.style arguments per diff line; the first match wins, so the file headers come
# before the single + and -
DIFF_LINE_STYLES = (
    ("+++", {"bold": True}),
    ("---", {"bold": True}),
    ("@@", {"fg": "cyan"}),
    ("+", {"fg": "green"}),
    ("-", {"fg": "red"}),
)

# a decorator named like one of these makes a method a property; setter, getter and
# deleter keep a property's parts together, because they share its name
PROPERTY_DECORATORS = ("property", "cached_property", "setter", "getter", "deleter")

# the module-level name that type statements go right after
LOGGER_NAME = "logger"

TRIPLE_QUOTES = ('"""', "'''")

# the line that starts an inline SQL string, right after its opening quotes; a marker line
# with more text on it is a comment that tailor must not turn into SQL
SQL_MARKER = "--sql\n"

# how syntaqlite lays out the SQL: 2-space indents, upper-case keywords, no semicolon
# added; each clause and subquery breaks onto its own lines only when it does not fit
SQL_FORMAT = {"indent_width": 2, "keyword_case": "upper", "semicolons": False}

# the SQL keeps at least this much width, however deep the string is indented
MINIMUM_SQL_WIDTH = 40

# SQLite goes to syntaqlite, every other dialect to sqruff under its own name
DEFAULT_SQL_DIALECT = "sqlite"
SQRUFF_DIALECTS = (
    "ansi",
    "athena",
    "bigquery",
    "clickhouse",
    "databricks",
    "db2",
    "duckdb",
    "exasol",
    "greenplum",
    "hive",
    "materialize",
    "mysql",
    "oracle",
    "postgres",
    "redshift",
    "snowflake",
    "sparksql",
    "sqlite",
    "starrocks",
    "teradata",
    "trino",
    "tsql",
)

# dialects whose unquoted table and column names can be case-sensitive: their SQL may only
# change keyword case; elsewhere an unquoted name reads the same in any case
CASE_SENSITIVE_NAME_DIALECTS = ("sqlite", "mysql")

# a database driver, as a file imports it and as a project depends on it, names its dialect
SQL_DRIVER_MODULES = {
    "sqlite3": "sqlite",
    "aiosqlite": "sqlite",
    "apsw": "sqlite",
    "psycopg": "postgres",
    "psycopg2": "postgres",
    "asyncpg": "postgres",
    "pg8000": "postgres",
    "duckdb": "duckdb",
    "pymysql": "mysql",
    "MySQLdb": "mysql",
    "mysql.connector": "mysql",
    "aiomysql": "mysql",
    "snowflake.connector": "snowflake",
    "google.cloud.bigquery": "bigquery",
    "clickhouse_connect": "clickhouse",
    "clickhouse_driver": "clickhouse",
}
SQL_DRIVER_PACKAGES = {
    "aiosqlite": "sqlite",
    "apsw": "sqlite",
    "psycopg": "postgres",
    "psycopg2": "postgres",
    "psycopg2-binary": "postgres",
    "asyncpg": "postgres",
    "pg8000": "postgres",
    "duckdb": "duckdb",
    "pymysql": "mysql",
    "mysqlclient": "mysql",
    "mysql-connector-python": "mysql",
    "aiomysql": "mysql",
    "snowflake-connector-python": "snowflake",
    "google-cloud-bigquery": "bigquery",
    "clickhouse-connect": "clickhouse",
    "clickhouse-driver": "clickhouse",
}

# the placeholders drivers fill in: %s and %(name)s, ?, ?1, $1, :name (not a :: cast), @name
# (not @@name); before layout each becomes a plain name every dialect reads as a value
SQL_PARAMETER_PATTERN = (
    r"%\(\w+\)s|%s\b|\?\d*|\$\d+|(?<![:\w]):[A-Za-z_]\w*|(?<![@\w])@[A-Za-z_]\w*"
)
SQL_PARAMETER_NAME = "tailor_"

# the opening of a PostgreSQL or Snowflake dollar-quoted value: $$ or $tag$
SQL_DOLLAR_QUOTE_PATTERN = r"\$(?:[A-Za-z_]\w*)?\$"

# the style sqruff lays SQL out in, with the dialect and the line width filled in per run
SQRUFF_CONFIG = """[sqruff]
dialect = {dialect}
rules = layout,capitalisation.keywords
max_line_length = {width}

[sqruff:indentation]
indent_unit = space
tab_space_size = 2

[sqruff:rules:capitalisation.keywords]
capitalisation_policy = upper
"""

# the oldest Python a project may target; tailor itself runs on the newest
MINIMUM_PROJECT_PYTHON = (3, 12)

# options that must be at least 1; every other number may be 0
POSITIVE_OPTIONS = ("line_length", "assignment_group_size")
