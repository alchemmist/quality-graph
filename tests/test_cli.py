import json
from pathlib import Path

import pytest

from qg_cli import __version__
from qg_cli.cli import main, parser
from quality_graph_core.result import Result
from quality_graph_core.schema import result_schema_value

PROVENANCE_ARGUMENTS = [
    "--repository",
    "owner/repository",
    "--pull-request",
    "42",
    "--head-sha",
    "a" * 40,
    "--workflow-run-id",
    "100",
    "--run-attempt",
    "1",
    "--graph-digest",
    "b" * 64,
]


def test_version_is_available_through_package_and_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert __version__ == "0.1.1"
    assert parser().prog == "qg"
    assert main([]) == 0
    assert "Quality Graph" in capsys.readouterr().out


def test_result_emit_and_validate_round_trip(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    arguments = [
        "result",
        "emit",
        "--node-id",
        "lint",
        "--title",
        "Lint",
        "--status",
        "failed",
        "--failure-kind",
        "quality",
        "--summary",
        "Failure",
        "--metric",
        "Findings=1",
        *PROVENANCE_ARGUMENTS,
        "--output",
        str(output),
    ]
    assert main(arguments) == 0
    assert main(["result", "validate", str(output)]) == 0
    result = Result.from_json(output.read_text())
    assert result.node_id == "lint"
    assert result.metrics[0].value == "1"


def test_result_commands_support_standard_streams(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "result",
        "emit",
        "--node-id",
        "lint",
        "--title",
        "Lint",
        "--status",
        "passed",
        *PROVENANCE_ARGUMENTS,
    ]
    assert main(arguments) == 0
    emitted = capsys.readouterr().out
    monkeypatch.setattr("sys.stdin.read", lambda: emitted)
    assert main(["result", "validate", "-"]) == 0


def test_result_schema_command_writes_published_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "schema.json"
    assert main(["result", "schema", "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == result_schema_value()
    assert main(["result", "schema"]) == 0
    assert json.loads(capsys.readouterr().out) == result_schema_value()


@pytest.mark.parametrize(
    "arguments",
    [
        ["result", "emit", "--metric", "invalid"],
        ["result", "validate", "missing.json"],
    ],
)
def test_result_commands_report_actionable_errors(arguments: list[str]) -> None:
    required = (
        [
            "--node-id",
            "lint",
            "--title",
            "Lint",
            "--status",
            "passed",
            *PROVENANCE_ARGUMENTS,
        ]
        if "emit" in arguments
        else []
    )
    with pytest.raises(SystemExit, match="2"):
        main([*arguments[:2], *required, *arguments[2:]])


def test_result_parent_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["result"]) == 0
    assert "Work with native result JSON" in capsys.readouterr().out


def test_project_commands_initialize_generate_and_validate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = "alchemmist/quality-graph@" + "a" * 40

    assert main(["init", "--root", str(tmp_path), "--runtime-action", runtime]) == 0
    assert main(["validate", "--root", str(tmp_path)]) == 1
    assert "missing generated file" in capsys.readouterr().err
    assert main(["generate", "--root", str(tmp_path)]) == 0
    assert main(["validate", "--root", str(tmp_path)]) == 0


def test_graph_schema_command_supports_file_and_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "graph-schema.json"
    assert main(["schema", "--output", str(output)]) == 0
    assert json.loads(output.read_text())["properties"]["version"] == {"const": 0}
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"]["version"] == {"const": 0}


def test_project_commands_report_invalid_roots(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["generate", "--root", str(tmp_path)])
