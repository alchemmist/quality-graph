from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from qg_cli.cli import main
from quality_graph_core.adapters import AdapterError, adapt_junit, adapt_native
from quality_graph_core.result import FailureKind, GitLabProvenance, ResultStatus
from quality_graph_core.schema import producer_schema_json
from tests.test_adapters import context

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue


def test_producer_report_receives_trusted_identity() -> None:
    selected = context()
    result = adapt_native(
        selected, b'{"reportVersion":0,"status":"passed","metrics":[{"label":"Files","value":"2"}]}'
    )
    assert result.node_id == selected.node_id
    assert result.title == selected.title
    assert result.provenance == selected.provenance
    assert result.metrics[0].value == "2"
    assert result.controls == ()


@pytest.mark.parametrize("field", ["nodeId", "title", "provenance", "controls", "schemaVersion"])
def test_producer_cannot_override_framework_fields(field: str) -> None:
    data = {"reportVersion": 0, "status": "passed", field: None}
    with pytest.raises(AdapterError, match="framework-owned"):
        adapt_native(context(), json.dumps(data).encode())


@pytest.mark.parametrize(
    "data",
    [
        {"reportVersion": 1, "status": "passed"},
        {"reportVersion": False, "status": "passed"},
        {"reportVersion": 0, "status": "in_progress"},
        {"reportVersion": 0, "status": "failed"},
        {"reportVersion": 0, "status": "passed", "failureKind": "quality"},
        {"reportVersion": 0, "status": "passed", "unknown": "value"},
    ],
)
def test_invalid_producer_data_fails_validation(data: dict[str, JsonValue]) -> None:
    with pytest.raises(AdapterError):
        adapt_native(context(), json.dumps(data).encode())


def test_passing_producer_cannot_hide_command_failure() -> None:
    result = adapt_native(context(succeeded=False), b'{"reportVersion":0,"status":"passed"}')
    assert result.status is ResultStatus.FAILED
    assert result.failure_kind is FailureKind.COMMAND
    assert "despite a passing report" in result.diagnostics[0].message


def test_producer_schema_command_and_committed_schema_agree(tmp_path: Path) -> None:
    path = tmp_path / "schema.json"
    assert main(["result", "schema", "--producer", "--output", str(path)]) == 0
    serialized = producer_schema_json()
    assert path.read_text() == serialized
    assert Path("schemas/producer-v0.schema.json").read_text() == serialized
    value = json.loads(serialized)
    assert value["required"] == ["reportVersion", "status"]
    assert "provenance" not in value["properties"]
    assert value["additionalProperties"] is False


def test_junit_traces_are_bounded_and_omissions_visible() -> None:
    cases = "".join(
        f'<testcase name="test-{number}"><failure>details</failure></testcase>'
        for number in range(101)
    )
    result = adapt_junit(context(), f"<testsuite>{cases}</testsuite>".encode())
    assert len(result.diagnostics) == 100
    assert len(result.findings) == 101
    assert result.notes == ("1 additional test traces omitted.",)


def test_junit_rejects_external_entities() -> None:
    report = b'<!DOCTYPE testsuite [<!ENTITY x SYSTEM "file:///missing">]><testsuite><testcase><failure>&x;</failure></testcase></testsuite>'
    with pytest.raises(AdapterError, match="invalid XML"):
        adapt_junit(context(), report)


@pytest.mark.parametrize("version", [0, 1, False])
def test_producer_report_binds_gitlab_schema_without_changing_input_version(
    *,
    version: int | bool,
) -> None:
    provenance = GitLabProvenance("https://gitlab.example.test", 1, "a" * 40, 10, 1, "b" * 64)
    selected = replace(context(), provenance=provenance)
    report = json.dumps({"reportVersion": version, "status": "passed"}).encode()
    if type(version) is int and version == 0:
        result = adapt_native(selected, report)
        assert result.provenance == provenance
        assert result.schema_version == 1
        assert result.status is ResultStatus.PASSED
    else:
        with pytest.raises(AdapterError):
            adapt_native(selected, report)


@pytest.mark.parametrize("count", [10_000, 10_001])
def test_large_junit_preserves_totals_and_bounded_findings(count: int) -> None:
    report = (
        "<testsuite>"
        + "".join(
            f'<testcase name="test-{index}"><failure>broken</failure></testcase>'
            for index in range(count)
        )
        + "</testsuite>"
    ).encode()
    result = adapt_junit(context(), report)
    assert result.failure_kind is FailureKind.QUALITY
    assert len(result.findings) == 10_000
    assert result.metrics[1].value == str(count)
    assert f"{count} failed" in result.summary
    assert ("1 additional findings omitted." in result.notes) == (count > 10_000)
