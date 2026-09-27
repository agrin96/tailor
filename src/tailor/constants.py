"""Fixed values the passes share. Settings a project can change are in config.Settings."""

import ast
from enum import StrEnum, nonmember


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
LOOPS_AND_BRANCHES = ast.For | ast.AsyncFor | ast.While | ast.If

# options that must be at least 1; every other number may be 0
POSITIVE_OPTIONS = ("line_length", "assignment_group_size")
