import ast
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

import anyio
import pytest

from qg_python import (
    diff,
    flaky,
    no_comments,
    object_annotations,
    suppressions,
    time_bombs,
    triple_quotes,
)
from qg_python.report import Finding, report
from qg_python.triple_quotes import scan_source as scan_triple_quotes


def test_diff_parser_handles_additions_deletions_renames_and_invalid_refs() -> None:
    patch = (
        "diff --git a/old.py b/new.py\n"
        "--- a/old.py\n"
        "+++ b/new.py\n"
        "@@ -1,2 +1,3 @@\n"
        " unchanged\n"
        "-removed\n"
        "+added\n"
        "+more\n"
        "\\ No newline at end of file"
    )

    assert diff.added_lines_by_path(patch) == {"new.py": frozenset({2, 3})}
    with pytest.raises(ValueError, match="invalid base"):
        diff.validated_base("main; unsafe")


def test_diff_loader_filters_missing_and_unselected_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "selected.py").write_text("value = 1\n")
    monkeypatch.setattr(
        "qg_python.diff.patch_for_base",
        lambda _base: (
            "diff --git a/selected.py b/selected.py\n"
            "+++ b/selected.py\n"
            "@@ -0,0 +1 @@\n"
            "+value = 1\n"
            "diff --git a/missing.py b/missing.py\n"
            "+++ b/missing.py\n"
            "@@ -0,0 +1 @@\n"
            "+missing = 1\n"
            "diff --git a/readme.md b/readme.md\n"
            "+++ b/readme.md\n"
            "@@ -0,0 +1 @@\n"
            "+text"
        ),
    )

    assert diff.changed_files("main", (".py",)) == (
        diff.ChangedFile("selected.py", "value = 1\n", frozenset({1})),
    )


def test_patch_runner_uses_validated_argv_and_requires_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []

    def run(
        _function: Callable[..., CompletedProcess[bytes]],
        command: list[str],
    ) -> CompletedProcess[bytes]:
        observed.append(command)
        return CompletedProcess(command, 0, b"patch")

    monkeypatch.setattr("qg_python.diff.anyio.run", run)
    assert diff.patch_for_base("origin/main") == "patch"
    assert observed[0][-2] == "origin/main...HEAD"

    monkeypatch.setattr("qg_python.diff.GIT", None)
    with pytest.raises(RuntimeError, match="git executable"):
        diff.patch_for_base("main")


def test_suppression_gate_detects_source_and_configuration_directives() -> None:
    directive = "# " + "noqa: S101"
    source = f"value = 1\nunsafe()  {directive}\n"
    config = '[tool.ruff.lint]\nignore = ["S101"]\n'

    source_findings = suppressions.scan_source("app.py", source, frozenset({2}))
    config_findings = suppressions.scan_source("pyproject.toml", config, frozenset({2}))

    expected_directive = directive.partition(":")[0]
    assert source_findings == (
        Finding("app.py", 2, 11, f"new suppression is not allowed: {expected_directive}"),
    )
    assert config_findings == (
        Finding("pyproject.toml", 2, 1, "new suppression is not allowed: ignore ="),
    )
    assert suppressions.scan_source("app.py", source, frozenset({1})) == ()


def test_object_annotation_gate_handles_nested_forward_and_invalid_annotations() -> None:
    source = "value: dict[str, object]\nother: 'list[object]'\n"

    findings = object_annotations.scan_source("app.py", source, frozenset({1, 2}))

    assert findings == (
        Finding(
            "app.py",
            1,
            18,
            "annotation must be more specific than object: dict[str, object]",
        ),
        Finding(
            "app.py",
            2,
            8,
            "annotation must be more specific than object: 'list[object]'",
        ),
    )
    assert object_annotations.scan_source("app.py", "value: [\n", frozenset({1}))[0].line == 1
    assert object_annotations.scan_source("app.py", "value: str\n", frozenset({1})) == ()
    functions = (
        "async def run(value: module.object, *args: object, **kwargs: str) -> object:\n"
        "    return value\n"
    )
    assert len(object_annotations.scan_source("app.py", functions, frozenset({1}))) == 3
    assert object_annotations.object_location(ast_constant("not [valid")) is None
    assert object_annotations.object_location(ast_constant("list[str]")) is None
    assert object_annotations.annotation_nodes(ast.parse("def plain(value: str): pass\n"))


def test_triple_quote_gate_checks_inline_opening_closing_and_token_errors() -> None:
    inline = 'value = """inline"""\n'
    opening = 'value = """content\nnext\n"""\n'
    closing = 'value = """\ncontent"""\n'

    assert scan_triple_quotes("app.py", inline, frozenset({1})) == (
        Finding("app.py", 1, 9, "one-line string must use quotes"),
    )
    assert scan_triple_quotes("app.py", opening, frozenset({1})) == (
        Finding("app.py", 1, 9, "triple quote must open alone"),
    )
    assert scan_triple_quotes("app.py", closing, frozenset({2})) == (
        Finding("app.py", 2, 8, "triple quote must close alone"),
    )
    assert scan_triple_quotes("app.py", 'value = """unterminated', frozenset({1}))
    assert scan_triple_quotes("app.py", opening, frozenset({2})) == ()
    assert scan_triple_quotes("app.py", 'value = "ordinary"\n', frozenset({1})) == ()
    assert scan_triple_quotes("app.py", inline, frozenset()) == ()
    fstring = 'value = f"""content {1}\nnext\n"""\n'
    assert scan_triple_quotes("app.py", fstring, frozenset({1})) == (
        Finding("app.py", 1, 10, "triple quote must open alone"),
    )
    assert scan_triple_quotes("app.py", 'value = f"ordinary"\n', frozenset({1})) == ()


def test_time_bomb_gate_classifies_units_and_ignores_unrelated_numbers() -> None:
    seconds = "1_700_" + "000_000"
    milliseconds = "1700000" + "000000"
    assert time_bombs.timestamp_unit(seconds) == "seconds"
    assert time_bombs.timestamp_unit("42") is None
    findings = time_bombs.scan_source(
        "app.py",
        f"safe = 42\nexpires = {milliseconds}\n",
        frozenset({2}),
    )
    assert findings == (
        Finding(
            "app.py",
            2,
            11,
            f"possible Unix timestamp in milliseconds: {milliseconds}",
        ),
    )
    assert time_bombs.scan_source("app.py", "value = 9999999999999999999\n", frozenset({1})) == ()


def test_no_comment_gate_allows_directives_and_reports_comments(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    directive = "# " + "noqa: S101"
    source.write_text(f"#!/usr/bin/env python\nvalue = 1  {directive}\nother = 2  # explanation\n")

    findings = no_comments.scan_file(source)

    assert len(findings) == 1
    assert findings[0].line == 3
    assert no_comments.python_files((tmp_path,)) == (source,)
    assert no_comments.main([str(tmp_path)]) == 1


def test_flaky_gate_selects_and_repeats_changed_python_tests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_feature.py").write_text("def test_feature(): pass\n")
    monkeypatch.setattr(
        "qg_python.flaky.patch_for_base",
        lambda _base: (
            "diff --git a/test_feature.py b/test_feature.py\n"
            "+++ b/test_feature.py\n"
            "@@ -0,0 +1 @@\n"
            "+def test_feature(): pass"
        ),
    )
    calls = []

    def run(command: tuple[str, ...], *, check: bool) -> CompletedProcess[str]:
        calls.append((command, check))
        return CompletedProcess(command, 0)

    monkeypatch.setattr(
        "qg_python.flaky.anyio.run",
        lambda _function, command: run(tuple(command), check=False),
    )

    assert flaky.changed_test_files("main") == ("test_feature.py",)
    assert flaky.repeat(("test_feature.py",), 3) == 0
    assert len(calls) == 3


def test_flaky_gate_returns_first_failure_and_report_writes_findings(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "qg_python.flaky.anyio.run",
        lambda _function, command: CompletedProcess(command, 1),
    )
    assert flaky.repeat(("test_feature.py",), 3) == 1
    assert "repeat 1/3" in capsys.readouterr().err

    finding = Finding("app.py", 1, 2, "failure")
    assert report((finding,)) == 1
    assert "app.py:1:2" in capsys.readouterr().out
    assert report(()) == 0


def test_flaky_process_adapter_disables_subprocess_checking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []

    async def run_process(command: list[str], *, check: bool) -> CompletedProcess[bytes]:
        observed.append((command, check))
        return CompletedProcess(command, 0)

    monkeypatch.setattr("qg_python.flaky.anyio.run_process", run_process)

    result = anyio.run(flaky.run_test, ["python", "-m", "pytest"])

    assert result.returncode == 0
    assert observed == [(["python", "-m", "pytest"], False)]


def test_gate_main_functions_handle_clean_and_failing_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    changed = diff.ChangedFile("app.py", "value: object\n", frozenset({1}))
    calls = []

    def selected(base: str, suffixes: tuple[str, ...]) -> tuple[diff.ChangedFile, ...]:
        calls.append((base, suffixes))
        return (changed,)

    for module in (suppressions, object_annotations, time_bombs, triple_quotes):
        monkeypatch.setattr(module, "changed_files", selected)
    assert object_annotations.main(["--base", "main"]) == 1
    assert suppressions.main(["--base", "main"]) == 0
    assert time_bombs.main(["--base", "main"]) == 0
    assert triple_quotes.main(["--base", "main"]) == 0
    assert calls == [
        ("main", (".py",)),
        ("main", suppressions.SUFFIXES),
        ("main", (".py", ".sh")),
        ("main", (".py",)),
    ]

    directive = "# " + "noqa: S101"
    suppression_change = diff.ChangedFile(
        "app.py",
        f"safe = 1\nunsafe()  {directive}\n",
        frozenset({1, 2}),
    )
    monkeypatch.setattr(
        suppressions,
        "changed_files",
        lambda base, suffixes: (
            (suppression_change,)
            if (base, suffixes) == ("origin/main", suppressions.SUFFIXES)
            else ()
        ),
    )
    assert suppressions.main([]) == 1

    monkeypatch.setattr("qg_python.flaky.changed_test_files", lambda _base: ())
    assert flaky.main(["--base", "main"]) == 0
    with pytest.raises(SystemExit):
        flaky.main(["--attempts", "1"])

    empty = tmp_path / "empty"
    empty.mkdir()
    assert no_comments.main([str(empty)]) == 0


def ast_constant(value: str) -> ast.Constant:
    return ast.Constant(value=value, lineno=1, col_offset=0)
