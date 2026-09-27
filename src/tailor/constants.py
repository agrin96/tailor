"""Fixed values the passes share. Settings a project can change are in config.Settings."""

import ast
from enum import IntEnum, StrEnum, nonmember


class Marker(StrEnum):
    """Text the formatter adds while it works, and removes again. The split marker is built
    from two parts and the widener is written as escapes, so this file contains neither.
    SPLIT: an extra operand that makes ruff put one operand per line.
    WIDENER: added to each keyword name while ruff runs, so `name=` is as wide as `name = `."""
    SPLIT = "__tailor_" + "split__"
    WIDENER = "\u01c2\u01c2"


class BracketText(StrEnum):
    """The six bracket characters. OPENERS and CLOSERS: the opening and the closing three."""
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


MAXIMUM_RUFF_PASSES = 10

# ruff adds this import to code that targets an older Python than LAZY_ANNOTATIONS_PYTHON
FUTURE_ANNOTATIONS_CONFIG = (
    'lint.isort.required-imports = ["from __future__ import annotations"]'
)

# loosest first: a wrapped expression splits at the loosest operator in it
OPERATOR_TIERS: tuple[dict[type[ast.operator], str], ...] = (
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

# a decorator named like one of these makes a method a property; setter, getter and
# deleter keep a property's parts together, because they share its name
PROPERTY_DECORATORS = ("property", "cached_property", "setter", "getter", "deleter")

LOGGER_NAME = "logger"

TRIPLE_QUOTES = ('"""', "'''")
STRING_PREFIX_LETTERS = "rRbBuU"

# the line that starts an inline SQL string, newline included: a marker line with more
# text on it is a SQL comment, not the start of SQL
SQL_MARKER = "--sql\n"

MINIMUM_SQL_WIDTH = 40
QUOTED_SQL_NAME_STARTS = ('"', "`", "[")


class SqlDialect(StrEnum):
    """The dialects sqruff knows, under sqruff's names. SQLite goes to syntaqlite instead.
    CASE_SENSITIVE_NAMES: the dialects whose unquoted names can be case-sensitive, so their
    layout can change only the case of keywords."""
    ANSI = "ansi"
    ATHENA = "athena"
    BIGQUERY = "bigquery"
    CLICKHOUSE = "clickhouse"
    DATABRICKS = "databricks"
    DB2 = "db2"
    DUCKDB = "duckdb"
    EXASOL = "exasol"
    GREENPLUM = "greenplum"
    HIVE = "hive"
    MATERIALIZE = "materialize"
    MYSQL = "mysql"
    ORACLE = "oracle"
    POSTGRES = "postgres"
    REDSHIFT = "redshift"
    SNOWFLAKE = "snowflake"
    SPARKSQL = "sparksql"
    SQLITE = "sqlite"
    STARROCKS = "starrocks"
    TERADATA = "teradata"
    TRINO = "trino"
    TSQL = "tsql"
    CASE_SENSITIVE_NAMES = nonmember((SQLITE, MYSQL))


class SqlTokenCategory(StrEnum):
    """The syntaqlite token categories that the SQL checks read."""
    KEYWORD = "keyword"
    STRING = "string"
    COMMENT = "comment"
    IDENTIFIER = "identifier"


SQL_DRIVER_MODULES = {
    "sqlite3": SqlDialect.SQLITE,
    "aiosqlite": SqlDialect.SQLITE,
    "apsw": SqlDialect.SQLITE,
    "psycopg": SqlDialect.POSTGRES,
    "psycopg2": SqlDialect.POSTGRES,
    "asyncpg": SqlDialect.POSTGRES,
    "pg8000": SqlDialect.POSTGRES,
    "duckdb": SqlDialect.DUCKDB,
    "pymysql": SqlDialect.MYSQL,
    "MySQLdb": SqlDialect.MYSQL,
    "mysql.connector": SqlDialect.MYSQL,
    "aiomysql": SqlDialect.MYSQL,
    "snowflake.connector": SqlDialect.SNOWFLAKE,
    "google.cloud.bigquery": SqlDialect.BIGQUERY,
    "clickhouse_connect": SqlDialect.CLICKHOUSE,
    "clickhouse_driver": SqlDialect.CLICKHOUSE,
}
SQL_DRIVER_PACKAGES = {
    "aiosqlite": SqlDialect.SQLITE,
    "apsw": SqlDialect.SQLITE,
    "psycopg": SqlDialect.POSTGRES,
    "psycopg2": SqlDialect.POSTGRES,
    "psycopg2-binary": SqlDialect.POSTGRES,
    "asyncpg": SqlDialect.POSTGRES,
    "pg8000": SqlDialect.POSTGRES,
    "duckdb": SqlDialect.DUCKDB,
    "pymysql": SqlDialect.MYSQL,
    "mysqlclient": SqlDialect.MYSQL,
    "mysql-connector-python": SqlDialect.MYSQL,
    "aiomysql": SqlDialect.MYSQL,
    "snowflake-connector-python": SqlDialect.SNOWFLAKE,
    "google-cloud-bigquery": SqlDialect.BIGQUERY,
    "clickhouse-connect": SqlDialect.CLICKHOUSE,
    "clickhouse-driver": SqlDialect.CLICKHOUSE,
}

# the placeholders drivers fill in: %s and %(name)s, ?, ?1, $1, :name (not a :: cast), @name
# (not @@name)
SQL_PARAMETER_PATTERN = (
    r"%\(\w+\)s|%s\b|\?\d*|\$\d+|(?<![:\w]):[A-Za-z_]\w*|(?<![@\w])@[A-Za-z_]\w*"
)
SQL_PARAMETER_NAME = "tailor_"

# the opening of a PostgreSQL or Snowflake dollar-quoted value: $$ or $tag$
SQL_DOLLAR_QUOTE_PATTERN = r"\$(?:[A-Za-z_]\w*)?\$"

SQRUFF_CONFIG = """[sqruff]
dialect = {dialect}
rules = layout,capitalisation.keywords
max_line_length = {width}

[sqruff:indentation]
indent_unit = space
tab_space_size = {indent_width}

[sqruff:rules:capitalisation.keywords]
capitalisation_policy = upper
"""

MINIMUM_PROJECT_PYTHON = (3, 12)

# the first Python that evaluates annotations only when they are read
LAZY_ANNOTATIONS_PYTHON = (3, 14)
