import json
from pathlib import Path

import pytest

from qg_cli.cli import main
from quality_graph_core.adapters import AdapterError, adapt_junit, adapt_native
from quality_graph_core.result import FailureKind, ResultStatus
from quality_graph_core.schema import producer_schema_json
from tests.test_adapters import context


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
def test_invalid_producer_data_fails_validation(data: dict[str, object]) -> None:
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
