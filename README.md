# tailor

tailor is an opinionated Python formatter. It runs `ruff format` first. Then it changes the result to a house style: one item per line, spaced keyword arguments, blank lines that group the code, and imports sorted by length.

Install it from PyPI as `pytailor`. The command is `tailor`.

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

After `tailor`:

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

tailor needs Python 3.12 or later. It installs ruff as a dependency.

tailor reads your code with the Python version that it runs on. Run it with a Python version that is at least the version your code targets. For example, t-strings need Python 3.14. The standard library import group also comes from this Python version.

```sh
uv tool install pytailor
```

You can also use `pipx install pytailor` or `pip install pytailor`.

## Usage

```sh
tailor src tests                  # format the files in place
tailor --check src                # list the files that change, write nothing
tailor --diff src                 # show the changes as a diff, write nothing
tailor --line-length 100 src      # replace the line length from pyproject.toml
```

With `--check` or `--diff`, the exit code is 1 if a file changes. tailor formats the files in parallel. It skips hidden folders and `__pycache__`.

### Format on save in Neovim

Add tailor to [conform.nvim](https://github.com/stevearc/conform.nvim):

```lua
require("conform").setup({
    formatters = {
        tailor = { command = "tailor", args = { "$FILENAME" }, stdin = false },
    },
    formatters_by_ft = { python = { "tailor" } },
    format_on_save = { timeout_ms = 1000 },
})
```

tailor finds its configuration and the local import groups from the folder of the file. conform.nvim writes its temporary copy of the buffer in the same folder, so this works.

### Continuous integration

Run `tailor --check src tests`. The step fails if a file is not formatted.

## Configuration

Put the options in the `[tool.tailor]` table of `pyproject.toml`. tailor uses the nearest `pyproject.toml` above each file. All options are optional. These are the defaults:

```toml
[tool.tailor]
line-length = 88
module-definition-blank-lines = 2
class-definition-blank-lines = 2
compact-class-lines = 5
assignment-group-size = 3
long-body-lines = 2
short-dunder-lines = 2
constructors = ["__new__", "__init__", "__post_init__"]
```

| Option | Default | What it sets |
|---|---|---|
| `line-length` | `line-length` of `[tool.ruff]`, else 88 | The maximum line length. |
| `module-definition-blank-lines` | 2 | Blank lines around top-level functions and classes. |
| `class-definition-blank-lines` | 2 | Blank lines around methods and nested classes. |
| `compact-class-lines` | 5 | A class with this many lines or fewer has no blank lines between its attributes. |
| `assignment-group-size` | 3 | The group size for a long run of assignments. |
| `long-body-lines` | 2 | A `for`, `while`, or `if` with a longer body gets a blank line after it. |
| `short-dunder-lines` | 2 | Dunders with a body of this many lines or fewer have 1 blank line between them. |
| `constructors` | `__new__`, `__init__`, `__post_init__` | The dunders that come first in a class. |

If an option is unknown or has the wrong type, tailor stops with an error that names the file.

## House style

The numbers in this section are the defaults. The configuration can change them.

### Line breaks

- If a call, definition, import, or literal does not fit on one line, each item goes on its own line.
- If a comprehension does not fit on one line, the element and each `for` and `if` clause go on their own lines.
- If an expression with `and`, `or`, or a binary operator (`+ - * / // % ** @ | & ^ << >>`) does not fit on one line, it goes in parentheses. Each operand goes on its own line, with the operator first.
- The split occurs at the operator that binds most loosely. For example, `a + b * c` splits at `+` only. An expression with a comparison or `not` at the top does not split at its arithmetic operators.
- If a conditional expression (`a if condition else b`) does not fit on one line, it goes in parentheses. The value, the `if` part, and the `else` part each go on their own line.
- Existing trailing commas do not keep a bracket split. If the items fit on one line, they go on one line.

### Spaces

- Keyword arguments and defaults, lambda defaults included, have one space on each side of `=`: `name = value`.

### Blank lines

- Top-level functions and classes have 2 blank lines around them. Methods and nested classes also have 2.
- In a class of 5 lines or fewer (blank lines not counted), there are no blank lines between the attributes.
- In a function body and at module level, single-line assignments in a row form a run. A run of up to 5 stays together. A run of 6 or more splits into groups of 3 to 5, as even as possible (6 is 3 + 3, 7 is 4 + 3). A run of 2 or more has a blank line after it.
- In a function body, a blank line also goes before a comment on its own line, between two statements that each span more than one line, and after an `if` that ends in `return`, `raise`, `continue`, or `break`.
- No blank line is added above a `return`.
- A `for`, `while`, or `if` with more than 2 lines in its body has a blank line after it.
- There are no blank lines between the `case` blocks of a `match`.
- tailor does not remove blank lines that you add in a function body.

### Class members

- Dunder methods move up: after the attributes at the top and before the other methods. The constructors (`__new__`, `__init__`, `__post_init__`) come first. The other dunders follow, shortest body first. Other methods keep their order.
- Dunders with a body of 1 or 2 lines have 1 blank line between them.
- All definitions of one dunder (for example, `@overload` stubs and the implementation) move together as one group, in their written order. The group is sorted by the total length of its bodies.
- A comment at the indentation of a method belongs to the method below it, also when blank lines are between them. The comment moves with that method. A comment with more indentation belongs to the end of the method above it.

### Imports

- The imports at the top of a file are in 3 groups: standard library, third party, local. In each group, the shortest import comes first. Imports that span more than one line come last.
- The names in a `from` import are also sorted by length.
- `import a, b` splits into one import for each module.
- Local imports are relative imports, and packages or modules in the project root or its `src/` folder. The project root is the folder of the nearest `pyproject.toml`.
- If comments are between the imports, or if two imports bind the same name, tailor does not change the import order.

## Use with ruff

- Do not run `ruff format` after tailor. Ruff reverses the keyword spacing, the comprehension layout, and the blank lines.
- Turn off the ruff rules that sort imports (`I`). They sort in a different order.
- If you enabled the pycodestyle rules for spaces around keyword `=` (E251, E252) or for blank lines (E30x), turn them off. They report the house style as errors.
- You can keep the other `ruff check` rules.

tailor uses the ruff formatter settings of your project, for example `quote-style` and `indent-width`.

## Safety and limits

- tailor parses the ruff output and its own result, and compares the two syntax trees. Only the import order and the order of dunders in a class can be different. If anything else is different, tailor writes nothing and shows an error.
- tailor moves dunders without a check of what they use when the class is created. If a decorator or a default value of a dunder uses another member of the class, the moved class can fail.
- Ruff keeps `# fmt: off` and `# fmt: skip` regions as they are. tailor does not know these markers: its own changes (keyword spacing, blank lines, class member order, imports) also occur in these regions.
- A file that contains the text `ǂǂ` is not formatted. tailor uses these two characters while ruff runs.

## License

MIT
