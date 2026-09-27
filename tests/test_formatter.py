import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from tailor.imports import sort_imports
from tailor.config import Settings, load_settings
from tailor.operators import remove_split_markers
from tailor import Mode, main, format_file, colored_diff, python_files, format_source

FIRST_PARTY = frozenset({"jobs", "worker"})


def run(source: str, line_length: int = 88) -> str:
    return format_source(
        source = textwrap.dedent(source),
        filename = "sample.py",
        first_party = FIRST_PARTY,
        settings = Settings(line_length = line_length),
    )


def test_house_style():
    source = """
        from worker.nodes import StepId, SkillStepNode, CompetencyLabel, CompetencyDescription, Edge
        import structlog
        from jobs.models import JobAborted
        import asyncio
        class Outlined(ProgressEvent):
            stage: ClassVar[JobEventType] = JobEventType.OUTLINED

            competencies: int
        class Builder:
            async def create(self, *, user_request: str, title: str, milestone: str, next_title: str) -> Response:
                prediction = await self.orchestration.predict(self._create_scope_anchor.acall, user_request=user_request)
                check = lambda answer: any(c.account.strip() and c.title.strip() for c in answer.chapters)
                return SkillStepNode(label=self.state.next_label(), name=card.name, keywords=set(card.keywords))
            def wire(self):
                outline = self.state.outline
                connectivity = self.state.connectivity
                skill_of = {c.label: c.skill for c in outline.competencies}
                steps = set(connectivity) - set(outline.checkpoints)
                return steps
        def helper(value=1):
            return [number for number in bundle.steps if 1 <= number <= len(steps) and number not in claimed]
    """

    expected = """\
        import asyncio

        import structlog

        from jobs.models import JobAborted
        from worker.nodes import (
            Edge,
            StepId,
            SkillStepNode,
            CompetencyLabel,
            CompetencyDescription,
        )


        class Outlined(ProgressEvent):
            stage: ClassVar[JobEventType] = JobEventType.OUTLINED
            competencies: int


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
                    self._create_scope_anchor.acall,
                    user_request = user_request,
                )

                check = lambda answer: any(
                    c.account.strip() and c.title.strip()
                    for c in answer.chapters
                )
                return SkillStepNode(
                    label = self.state.next_label(),
                    name = card.name,
                    keywords = set(card.keywords),
                )


            def wire(self):
                outline = self.state.outline
                connectivity = self.state.connectivity
                skill_of = {c.label: c.skill for c in outline.competencies}
                steps = set(connectivity) - set(outline.checkpoints)
                return steps


        def helper(value = 1):
            return [
                number
                for number in bundle.steps
                if 1 <= number <= len(steps) and number not in claimed
            ]
    """

    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_keyword_spacing_that_overflows_explodes_the_call():
    fits_only_without_spaces = (
        "result = function_name(first_argument=alpha, second=beta)\n"
    )

    result = run(
        fits_only_without_spaces,
        line_length = len(fits_only_without_spaces) - 1,
    )

    assert (
        result
        == "result = function_name(\n    first_argument = alpha,\n    second = beta,\n)\n"
    )


def test_strings_subscripts_and_generator_arguments_keep_their_syntax():
    source = 'value = mapping[key](f"{name=} {dict(a=1)}", *(item for item in items))\n'

    assert (
        run(source)
        == 'value = mapping[key](f"{name=} {dict(a=1)}", *(item for item in items))\n'
    )


def test_imports_with_comments_between_them_keep_their_order():
    source = "import zlib\n# the parser needs this first\nimport ast\n\nvalue = 1\n"
    assert sort_imports(source = source, first_party = FIRST_PARTY) == source


def test_parenthesized_match_subject_stays_a_single_value():
    source = "match (\n    value\n):\n    case 1:\n        pass\n"
    assert run(source) == "match value:\n    case 1:\n        pass\n"


def test_import_statements_split_into_groups_and_shadowed_names_keep_their_order():
    mixed = "import requests, os, jobs.models as models\n"
    shadowed = "from math import factorial as f, cos as f\n"

    assert (
        sort_imports(source = mixed, first_party = FIRST_PARTY)
        == "import os\n\nimport requests\n\nimport jobs.models as models\n"
    )

    assert sort_imports(source = shadowed, first_party = FIRST_PARTY) == shadowed


def test_unaliased_import_colliding_with_an_alias_keeps_its_order():
    source = "from math import cos as os\nimport os\n"
    assert sort_imports(source = source, first_party = FIRST_PARTY) == source


def test_overlapping_arguments_list_each_file_once(tmp_path, monkeypatch):
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "module.py").write_text("value = 1\n")
    monkeypatch.chdir(tmp_path)
    files = python_files(
        paths = [
            Path("package"),
            Path("package/module.py"),
            tmp_path / "package" / "module.py",
        ]
    )
    assert files == [Path("package/module.py")]


def test_files_come_from_ruff_discovery(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nextend-exclude = ["generated"]\n',
    )

    for name in [
        "package/module.py",
        "package/stub.pyi",
        ".venv/lib.py",
        "venv/lib.py",
        "generated/code.py",
    ]:
        (tmp_path / name).parent.mkdir(parents = True, exist_ok = True)
        (tmp_path / name).write_text("value = 1\n")
    monkeypatch.chdir(tmp_path)
    assert python_files(paths = [Path(".")]) == [Path("package/module.py")]
    assert python_files(paths = [Path("venv/lib.py")]) == [Path("venv/lib.py")]


def test_wrapped_boolean_expression_puts_each_operand_on_its_own_line():
    source = """
        def blocks():
            compact_child = isinstance(statement, ast.ClassDef) and is_compact(statement=statement, lines=lines)
            compact_child = isinstance(statement, ast.ClassDef) and is_compact(statement=statement, lines=lines, other_argument=other_value)
    """

    expected = """\
        def blocks():
            compact_child = (
                isinstance(statement, ast.ClassDef)
                and is_compact(statement = statement, lines = lines)
            )

            compact_child = (
                isinstance(statement, ast.ClassDef)
                and is_compact(
                    statement = statement,
                    lines = lines,
                    other_argument = other_value,
                )
            )
    """

    assert run(source) == textwrap.dedent(expected)


def test_call_split_on_an_earlier_run_joins_again_when_it_fits():
    assert (
        run("result = function(\n    first = 1,\n    second = 2,\n)\n")
        == "result = function(first = 1, second = 2)\n"
    )


def test_marker_name_already_in_the_code_is_left_alone():
    source = (
        'message = "keep and __tailor_split__"  # and __tailor_split__\n'
        "flag = first_condition_value and check_something(argument_one=1, argument_two=2, three=3)\n"
    )
    result = run(source)
    assert result.count("__tailor_split__") == 2
    assert run(result) == result


def test_boolean_operands_split_inside_call_arguments_and_comprehension_filters():
    source = """
        consume(isinstance(statement, ast.ClassDef) and is_compact(statement=statement, lines=lines, other=value))
        values = [item for item in items if isinstance(statement, ast.ClassDef) and is_compact(statement=statement, lines=lines)]
    """

    expected = """\
        consume(
            isinstance(statement, ast.ClassDef)
            and is_compact(statement = statement, lines = lines, other = value)
        )
        values = [
            item
            for item in items
            if isinstance(statement, ast.ClassDef)
            and is_compact(statement = statement, lines = lines)
        ]
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_long_loop_bodies_get_a_blank_line_after_and_match_cases_none_between():
    source = """
        def children(statement):
            for field in fields:
                value = getattr(statement, field, None)
                if value:
                    yield value
            for handler in handlers:
                yield handler.body
            match statement:
                case ast.If():
                    return 1

                case _:
                    return 0
    """

    expected = """\
        def children(statement):
            for field in fields:
                value = getattr(statement, field, None)
                if value:
                    yield value

            for handler in handlers:
                yield handler.body

            match statement:
                case ast.If():
                    return 1
                case _:
                    return 0
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_blank_line_before_a_parenthesized_case_pattern_is_removed():
    source = """
        match event:
            case None:
                handle_missing()

            case VeryLongFirstEventVariantName() | VeryLongSecondEventVariantName() | VeryLongThirdEventVariantName():
                handle_event()
    """

    expected = """\
        match event:
            case None:
                handle_missing()
            case (
                VeryLongFirstEventVariantName()
                | VeryLongSecondEventVariantName()
                | VeryLongThirdEventVariantName()
            ):
                handle_event()
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_case_pattern_names_starting_with_case_do_not_hide_the_header():
    source = """
        match event:
            case None:
                handle_missing()

            case cases.VeryLongFirstEventVariantName() | cases.VeryLongSecondEventVariantName() | cases.Third():
                handle_event()
    """
    result = run(source)
    assert "handle_missing()\n    case (\n        cases.VeryLong" in result
    assert run(result) == result


def test_assignment_runs_split_into_groups_of_three_to_five():
    source = """
        def build(items):
            first = 1
            second = 2
            third = 3
            fourth = 4
            fifth = 5
            sixth = 6
            seventh = 7
            if not items:
                return None
            # totals come next
            total = sum(items)
            return total
    """

    expected = """\
        def build(items):
            first = 1
            second = 2
            third = 3
            fourth = 4

            fifth = 5
            sixth = 6
            seventh = 7

            if not items:
                return None

            # totals come next
            total = sum(items)
            return total
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_wrapped_conditional_expression_splits_before_if_and_else():
    source = 'operator = "or" if any(is_code(token=child, string="or") for child in children) else "and"\n'
    expected = """\
        operator = (
            "or"
            if any(is_code(token = child, string = "or") for child in children)
            else "and"
        )
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_module_constants_split_into_groups_of_three_to_five():
    source = "import ast\n\nFIRST = 1\nSECOND = 2\nTHIRD = 3\nFOURTH = 4\nFIFTH = 5\nSIXTH = 6\nSEVENTH = 7\nprint(FIRST)\n"
    expected = "import ast\n\nFIRST = 1\nSECOND = 2\nTHIRD = 3\nFOURTH = 4\n\nFIFTH = 5\nSIXTH = 6\nSEVENTH = 7\n\nprint(FIRST)\n"
    result = run(source)

    assert result == expected
    assert run(result) == result


def test_dunders_move_up_constructors_first_then_shortest_first():
    source = """
        class Node:
            kind: str
            def walk(self):
                return self.children
            def __eq__(self, other):
                if not isinstance(other, Node):
                    return NotImplemented
                return self.kind == other.kind
            def __init__(self, kind):
                self.kind = kind
            def __len__(self):
                return self.size
            def __hash__(self):
                return hash(self.kind)
    """

    expected = """\
        class Node:
            kind: str


            def __init__(self, kind):
                self.kind = kind

            def __len__(self):
                return self.size

            def __hash__(self):
                return hash(self.kind)


            def __eq__(self, other):
                if not isinstance(other, Node):
                    return NotImplemented
                return self.kind == other.kind


            def walk(self):
                return self.children
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_overloaded_dunders_move_as_a_group_and_comments_move_with_the_method_below():
    source = """
        class Sequence:
            def run(self):
                return 1


            # indexing

            @overload
            def __getitem__(self, index: int) -> int: ...

            @overload
            def __getitem__(self, index: slice) -> list: ...

            def __getitem__(self, index):
                return index


            # size
            def __len__(self):
                return 0
    """

    expected = """\
        class Sequence:
            # size
            def __len__(self):
                return 0

            # indexing

            @overload
            def __getitem__(self, index: int) -> int: ...

            @overload
            def __getitem__(self, index: slice) -> list: ...

            def __getitem__(self, index):
                return index


            def run(self):
                return 1
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_comprehension_that_keyword_spacing_pushes_too_wide_splits_its_clauses():
    source = "rest = [member for member in members if not is_dunder_definition(statement=member)]\n"
    expected = "rest = [\n    member\n    for member in members\n    if not is_dunder_definition(statement = member)\n]\n"
    result = run(source, line_length = len(source) - 1)

    assert result == expected
    assert run(result, line_length = len(source) - 1) == result


def test_trailing_comment_of_the_last_method_moves_with_it():
    source = """
        class Example:
            def regular(self):
                pass


            def __len__(self):
                return 0
                # explanation belonging to __len__
    """

    expected = """\
        class Example:
            def __len__(self):
                return 0
                # explanation belonging to __len__


            def regular(self):
                pass
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_multiline_class_header_stays_in_place_when_a_dunder_moves_up():
    source = """
        class Example(
            FirstVeryLongBaseClassNameForThisExample,
            SecondVeryLongBaseClassNameForThisExample,
        ):
            # regular work
            def regular(self):
                pass


            def __len__(self):
                return 0
    """

    expected = """\
        class Example(
            FirstVeryLongBaseClassNameForThisExample,
            SecondVeryLongBaseClassNameForThisExample,
        ):
            def __len__(self):
                return 0


            # regular work
            def regular(self):
                pass
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_wrapped_binary_expression_puts_each_operand_on_its_own_line():
    source = """
        def f():
            floor_rows = [body[start - 1].end_lineno if start else node.lineno] + [member.end_lineno for member in members[:-1]]
            total = base_amount_value + compute_the_adjustment(first_argument=1, second_argument=2) * factor
    """

    expected = """\
        def f():
            floor_rows = (
                [body[start - 1].end_lineno if start else node.lineno]
                + [member.end_lineno for member in members[:-1]]
            )

            total = (
                base_amount_value
                + compute_the_adjustment(first_argument = 1, second_argument = 2) * factor
            )
    """
    result = run(source)
    assert result == textwrap.dedent(expected)
    assert run(result) == result


def test_formatting_disabled_regions_keep_their_keywords_and_settle():
    source = """
        # fmt: off
        f(x = 1)
        result = base ** exponent(
            argument,
        )
        # fmt: on
        g(y = 2)  # fmt: skip
    """
    result = run(source)
    assert "f(x = 1)" in result and "g(y = 2)" in result
    assert "__tailor_split__" not in result and "ǂ" not in result
    assert run(result) == result


def test_lambda_defaults_are_spaced_inside_and_outside_calls():
    source = (
        "consume(lambda x=1, y=2: x + y)\nhandler = lambda first=1, second=2: first\n"
    )
    expected = "consume(lambda x = 1, y = 2: x + y)\nhandler = lambda first = 1, second = 2: first\n"
    result = run(source)

    assert result == expected
    assert run(result) == result


def test_lambda_defaults_after_a_nested_lambda_and_inside_debug_fstrings():
    source = 'handler = lambda first=lambda: 1, second=2: second\nmessage = f"{(lambda x=1: x)=}"\n'
    expected = 'handler = lambda first = lambda: 1, second = 2: second\nmessage = f"{(lambda x=1: x)=}"\n'
    result = run(source)

    assert result == expected
    assert run(result) == result


def test_split_markers_are_removed_also_when_ruff_writes_the_power_compact():
    compact = "value = (\n    first_operand**second_operand**__tailor_split__\n)\n"
    spaced = "value = (\n    first_operand\n    ** second_operand\n    ** __tailor_split__\n)\n"

    assert (
        remove_split_markers(source = compact)
        == "value = (\n    first_operand**second_operand\n)\n"
    )

    assert (
        remove_split_markers(source = spaced)
        == "value = (\n    first_operand\n    ** second_operand\n)\n"
    )
    source = "result = compute_value(first_argument_name, second_argument_name, third_argument_name, fourth_argument_name) ** exponent\n"
    result = run(source)

    assert "__tailor_split__" not in result
    assert run(result) == result


def test_settings_come_from_tool_tailor_with_ruff_line_length_as_fallback(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n\n[tool.tailor]\nshort-dunder-lines = 1\n"
        'constructors = ["__init__"]\n'
    )
    settings = load_settings(root = tmp_path)
    assert settings == Settings(
        line_length = 100,
        short_dunder_lines = 1,
        constructors = ("__init__",),
    )

    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "pyproject.toml").write_text("[tool.tailor]\nshort-dunder-line = 1\n")
    with pytest.raises(
        ValueError,
        match = "unknown \\[tool.tailor\\] option: short-dunder-line",
    ):
        load_settings(root = wrong)


def test_diff_mode_shows_the_change_and_writes_nothing(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    module = tmp_path / "module.py"
    module.write_text("value = call(first=1)\n")
    result = format_file(path = module, mode = Mode.DIFF, line_length = None)
    assert result.changed
    assert "-value = call(first=1)\n+value = call(first = 1)\n" in result.diff
    assert module.read_text() == "value = call(first=1)\n"


def test_diff_of_a_file_without_a_final_newline_is_a_valid_patch(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    changed = tmp_path / "changed.py"
    changed.write_text("value = call(first=1)")
    newline_only = tmp_path / "newline_only.py"
    newline_only.write_text("value = 1")
    marker = "\\ No newline at end of file\n"
    changed_diff = format_file(
        path = changed,
        mode = Mode.DIFF,
        line_length = None,
    ).diff
    assert f"-value = call(first=1)\n{marker}+value = call(first = 1)\n" in changed_diff
    newline_diff = format_file(
        path = newline_only,
        mode = Mode.DIFF,
        line_length = None,
    ).diff
    assert newline_diff.endswith(f"-value = 1\n{marker}+value = 1\n")


def test_config_section_that_is_not_a_table_is_a_file_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool]\ntailor = 1\n")
    module = tmp_path / "module.py"
    module.write_text("value = 1\n")
    result = format_file(path = module, mode = Mode.CHECK, line_length = None)
    assert (
        result.error == f"{tmp_path / 'pyproject.toml'}: [tool.tailor] must be a table"
    )


def test_force_exclude_also_skips_a_file_named_directly(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.ruff]\nforce-exclude = true\nextend-exclude = ["generated"]\n',
    )
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "code.py").write_text("value = 1\n")
    monkeypatch.chdir(tmp_path)
    assert python_files(paths = [Path("generated/code.py")]) == []


def test_a_folder_and_a_symlink_to_it_list_each_file_once(tmp_path, monkeypatch):
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "module.py").write_text("value = 1\n")
    (tmp_path / "alias").symlink_to(tmp_path / "package", target_is_directory = True)
    monkeypatch.chdir(tmp_path)
    assert len(python_files(paths = [Path("package"), Path("alias")])) == 1


def test_colored_diff_colors_lines_by_their_prefix():
    diff = "--- a.py\n+++ a.py\n@@ -1 +1 @@\n-value=1\n+value = 1\n unchanged\n"
    assert colored_diff(diff = diff) == (
        "\033[1m--- a.py\033[0m\n"
        "\033[1m+++ a.py\033[0m\n"
        "\033[36m@@ -1 +1 @@\033[0m\n"
        "\033[31m-value=1\033[0m\n"
        "\033[32m+value = 1\033[0m\n"
        " unchanged\n"
    )


def test_command_line_rejects_a_missing_path_and_check_with_diff(tmp_path, monkeypatch):
    (tmp_path / "module.py").write_text("value = 1\n")
    monkeypatch.chdir(tmp_path)
    missing = CliRunner().invoke(main, ["missing.py"])
    assert missing.exit_code == 2 and "'missing.py' does not exist" in missing.output
    both = CliRunner().invoke(main, ["--check", "--diff", "module.py"])
    assert (
        both.exit_code == 2
        and "--check and --diff cannot be used together" in both.output
    )


def test_redirected_diff_keeps_escape_characters_from_the_source(tmp_path, monkeypatch):
    (tmp_path / "module.py").write_text("value=1  # \x1b[31mred\x1b[0m\n")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["--diff", "module.py"])
    assert result.exit_code == 1
    assert (
        "-value=1  # \x1b[31mred\x1b[0m\n+value = 1  # \x1b[31mred\x1b[0m\n"
        in result.output
    )
