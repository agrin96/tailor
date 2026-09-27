<p align="center">
  <img src="https://raw.githubusercontent.com/agrin96/tailor/main/tailor-threadline.png" alt="tailor logo" width="128">
</p>

<h1 align="center">tailor</h1>

<p align="center">
  An opinionated Python formatter: <code>ruff format</code>, then a house style.
</p>

<p align="center">
  <a href="https://pypi.org/project/pytailor/"><img src="https://img.shields.io/pypi/v/pytailor" alt="PyPI version"></a>
  <a href="https://pypi.org/project/pytailor/"><img src="https://img.shields.io/pypi/pyversions/pytailor" alt="Python versions"></a>
  <a href="https://github.com/agrin96/tailor/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/pytailor" alt="License"></a>
</p>

tailor runs `ruff format`, then puts one item on each line, spaces keyword arguments, groups the code with blank lines and sorts imports by length. It also lays out the SQL in `"""--sql` strings.

## Example

Before:

```python
import structlog
from jobs.models import JobAborted
import asyncio

class Builder:
    async def create(self, *, user_request: str, title: str, milestone: str, next_title: str) -> Response:
        prediction = await self.orchestration.predict(self.anchor.acall, user_request=user_request)
        numbers = [number for number in bundle.steps if 1 <= number <= len(steps) and number not in claimed]
        return Node(label=self.state.next_label(), name=card.name, keywords=set(card.keywords))
```

After:

```python
import asyncio

import structlog

from jobs.models import JobAborted


class Builder:
    async def create(
        self,
        *,
        user_request: str,
        title: str,
        milestone: str,
        next_title: str,
    ) -> Response:
        prediction = await self.orchestration.predict(
            self.anchor.acall,
            user_request = user_request,
        )

        numbers = [
            number
            for number in bundle.steps
            if 1 <= number <= len(steps) and number not in claimed
        ]
        return Node(
            label = self.state.next_label(),
            name = card.name,
            keywords = set(card.keywords),
        )
```

## Installation

```sh
uv tool install pytailor
```

tailor runs on Python 3.14, and `uv` gets it for you. It formats projects that target Python 3.12 to 3.14. It reads the target the same way ruff does. If a project targets another version, tailor stops before it changes a file. For a project that targets Python 3.12 or 3.13, ruff adds `from __future__ import annotations` to each file.

## Usage

```sh
tailor src tests                  # format in place
tailor --check src                # list the files that would change
tailor --diff src                 # show the changes as a colored diff
tailor --line-length 100 src      # replace the line length for this run
```

tailor formats the `.py` files that `ruff check` finds: ruff's default exclusions, `exclude` and `extend-exclude` in `[tool.ruff]`, and `.gitignore` apply. The excludes of `[tool.ruff.lint]` and `[tool.ruff.format]` do not apply. With `--check` or `--diff`, the exit code is 1 when a file would change, so `tailor --check src tests` works as a CI step.

To format on save in Neovim, add tailor to [conform.nvim](https://github.com/stevearc/conform.nvim):

```lua
require("conform").setup({
    formatters = {
        tailor = { command = "tailor", args = { "$FILENAME" }, stdin = false },
    },
    formatters_by_ft = { python = { "tailor" } },
    format_on_save = { timeout_ms = 1000 },
})
```

## Configuration

Put the options in `[tool.tailor]` of `pyproject.toml`. tailor uses the nearest `pyproject.toml` above each file. An unknown option or a wrong type stops tailor with an error that names the file.

| Option | Default | What it sets |
|---|---|---|
| `line-length` | ruff's `line-length`, else 88 | The maximum line length. |
| `module-definition-blank-lines` | 2 | Blank lines around top-level functions and classes. |
| `class-definition-blank-lines` | 2 | Blank lines around methods and nested classes. |
| `compact-class-lines` | 5 | A class this short has no blank lines between its attributes. |
| `assignment-group-size` | 3 | The group size for a long run of assignments. |
| `long-body-lines` | 2 | A `for`, `while` or `if` with a longer body gets a blank line after it. |
| `short-dunder-lines` | 2 | Dunders this short have 1 blank line between them. |
| `constructors` | `__new__`, `__init__`, `__post_init__` | The dunders that come first in a class. |
| `sql-dialect` | found for each file | The dialect of `--sql` strings, for example `"postgres"`. |
| `sql-indent-width` | 2 | The indent of a SQL clause or subquery. |

## House style

The numbers below are the defaults.

- **Line breaks.** A call, definition, import or literal that does not fit puts each item on its own line. A long comprehension puts each `for` and `if` on its own line. A long `and`/`or`, operator or conditional expression goes in parentheses, one operand on each line, operator first.
- **Spaces.** Keyword arguments and defaults are written `name = value`.
- **Blank lines.** 2 around top-level definitions and methods, and after the imports. Runs of 6 or more assignments split into groups of 3 to 5. A long `for`, `while` or `if` gets a blank line after it.
- **Classes.** Dunders, classmethods and properties move up, in that order, constructors first. Overloads and property setters move with their group.
- **Imports.** Standard library, third party, local. Shortest first in each group.

<details>
<summary>All the rules</summary>

#### Line breaks

- If a call, definition, import or literal does not fit on one line, each item goes on its own line.
- If a comprehension does not fit on one line, the element and each `for` and `if` clause go on their own lines.
- If an expression with `and`, `or` or a binary operator (`+ - * / // % ** @ | & ^ << >>`) does not fit on one line, it goes in parentheses. Each operand goes on its own line, with the operator first.
- The split occurs at the operator that binds most loosely. For example, `a + b * c` splits at `+` only. An expression with a comparison or `not` at the top does not split at its arithmetic operators.
- If a conditional expression (`a if condition else b`) does not fit on one line, it goes in parentheses. The value, the `if` part and the `else` part each go on their own line.
- Existing trailing commas do not keep a bracket split. If the items fit on one line, they go on one line.

#### Blank lines

- Top-level functions and classes have 2 blank lines around them. The imports at the top of a file also have 2 blank lines after them.
- Methods and nested classes have 2 blank lines between them. The first method has 1 blank line above it.
- A class docstring has no blank line after it, before the class attributes.
- In a class of 5 lines or fewer, blank lines not counted, there are no blank lines between the attributes.
- Single-line assignments in a row form a run. A run of up to 5 stays together. A run of 6 or more splits into groups of 3 to 5, as even as possible: 6 is 3 + 3, 7 is 4 + 3. A run of 2 or more has a blank line after it.
- In a function body, a blank line also goes before a comment on its own line, between two statements that each span more than one line, and after an `if` that ends in `return`, `raise`, `continue` or `break`.
- No blank line is added above a `return`, and tailor does not remove blank lines that you add in a function body.
- A `for`, `while` or `if` with more than 2 lines in its body has a blank line after it.
- There are no blank lines between the `case` blocks of a `match`.

#### Classes

- Dunder methods, classmethods and properties move up, after the attributes and before the other methods, in that order. Other methods keep their order.
- The constructors come first among the dunders. The other dunders, the classmethods and the properties each follow shortest body first.
- All definitions of one name move together, in their written order: `@overload` stubs and the implementation, or a property and its setter.
- Dunders with a body of 1 or 2 lines have 1 blank line between them.
- A comment at the indentation of a method moves with the method below it. A deeper comment stays at the end of the method above it.
- The closing quotes of a class docstring go on its last line of text, if that line then fits.

#### Imports and type statements

- The imports at the top of a file are in 3 groups: standard library, third party, local. In each group, the shortest import comes first. Imports that span more than one line come last. The names in a `from` import are also sorted by length.
- `import a, b` splits into one import for each module.
- Local imports are relative imports, and the packages and modules in the project root or its `src/` folder.
- If comments are between the imports, or two imports bind the same name, the import order stays as written.
- Module-level `type` statements move up to after the imports and the `logger = ...` line, unless a statement above them uses their name.
- An alias value can use a module-level function, class or constant as a value: in a call, or in the metadata of `Annotated`. That definition moves up above the aliases, with its decorators and the comments above it. The definitions that it uses when Python defines it move too. Function bodies do not count. Annotations count only if tailor finds no target Python for the project.
- If such a definition cannot move, the aliases keep their place, and tailor shows a warning with the alias and the name. For example, a definition that uses an alias in a default value cannot move, and a name that the file binds twice cannot move.

</details>

## SQL

A string whose first line is `"""--sql` holds SQL. tailor lays out SQLite SQL with [syntaqlite](https://pypi.org/project/syntaqlite/) and other dialects with [sqruff](https://github.com/quarylabs/sqruff):

```python
row = db.fetch_one(
    """--sql
    SELECT
      @assists
      - (
        SELECT COUNT(*)
        FROM jobs
        WHERE
          json_extract(data, '$.user_id') = @user
          AND status NOT IN ('FAILED', 'CANCELLED')
      ) AS assists_left
    """,
    bindings = {"user": user, "assists": assists},
)
```

A clause that fits stays on one line. Keywords are upper case, and the SQL starts at the column of the opening quotes. tailor compares the SQL tokens before and after: if more than whitespace or case would change, or the SQL does not parse, the string stays as written.

tailor finds the dialect of each file in this order:

1. The `sql-dialect` option.
2. The `dialect` in the nearest `.sqruff` or `.sqlfluff` file.
3. The database driver that the file imports, for example `import duckdb`.
4. The database driver that the project depends on in `pyproject.toml`.
5. SQLite. If the project depends on drivers of two databases, tailor also shows a warning.

<details>
<summary>SQL details</summary>

- Driver placeholders stay as written: `?`, `?1`, `$1`, `:name`, `@name`, `%s` and `%(name)s`. SQLite has no `%s` or `%(name)s`, so SQLite SQL with them stays as written.
- The SQL stays as written if the string has a backslash, a dollar-quoted value (`$$...$$`), a quoted value or name across a line break, or two quoted values separated by a line break.
- A first line with more text after `--sql` is a SQL comment, and the string stays as written.
- Outside SQLite and MySQL, the case of unquoted names can also change.
- sqruff lays out all the SQL of a file at one width: the width of the string that is indented the most.
- The dialect names are sqruff's: `ansi`, `athena`, `bigquery`, `clickhouse`, `databricks`, `db2`, `duckdb`, `exasol`, `greenplum`, `hive`, `materialize`, `mysql`, `oracle`, `postgres`, `redshift`, `snowflake`, `sparksql`, `sqlite`, `starrocks`, `teradata`, `trino` and `tsql`.

</details>

## Notes

- Do not run `ruff format` after tailor. It reverses the keyword spacing, the comprehension layout and the blank lines.
- Turn off ruff's import sorting (`I`), and the pycodestyle rules for `=` spacing (E251, E252) and blank lines (E30x). The other `ruff check` rules can stay. tailor uses your ruff formatter settings, such as `quote-style`.
- tailor compares the syntax tree of ruff's output with its own result. If anything other than the order of the imports, the class members, the `type` statements and the definitions that the aliases use changed, it writes nothing and shows an error.
- tailor moves class members without a check of what they use when the class is created. A decorator or default value that uses another member of the class can fail after the move.
- A definition that moves above the aliases runs earlier when Python loads the module. If its decorator or its value has a side effect, that side effect also occurs earlier. An example is the registration order of routes.
- tailor also changes `# fmt: off` and `# fmt: skip` regions. A file that contains `ǂǂ` is not formatted: tailor uses these characters while ruff runs.

## License

MIT
