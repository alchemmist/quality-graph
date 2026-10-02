import json

import pytest
from scripts.check_report import combine

from quality_graph_core.adapters import AdapterError, adapt_junit, adapt_native, adapt_sarif
from quality_graph_core.graph import Graph
from quality_graph_core.policy import ApprovalTarget, effective_graph
from quality_graph_core.result import ControlKind, FailureKind, Result, ResultStatus
from tests.test_adapters import context


def test_partial_file_approval_cannot_hide_omitted_junit_failures() -> None:
    cases = "".join(
        f'<testcase name="case-{index}" file="approved.py"><failure>bad</failure></testcase>'
        for index in range(10_000)
    )
    cases += '<testcase name="last" file="unapproved.py"><failure>bad</failure></testcase>'
    result = adapt_junit(context(succeeded=False), f"<testsuite>{cases}</testsuite>".encode())
    graph = Graph.from_yaml(
        "version: 0\nprofiles: {default: {}}\nnodes:\n"
        "  lint:\n    title: Lint\n    run: lint\n"
        "    policy:\n      approvals: {findings: false, files: true, node: true}\n"
    )
    partial = {ApprovalTarget(ControlKind.FILE, "approved.py")}
    assert (
        effective_graph(graph, {"lint": result}, partial).results["lint"].status
        is ResultStatus.FAILED
    )
    assert result.to_value()["omittedFindings"] == 1
    assert Result.from_json(result.to_json()) == result
    whole = {ApprovalTarget(ControlKind.NODE, "lint")}
    assert (
        effective_graph(graph, {"lint": result}, whole).results["lint"].status
        is ResultStatus.PASSED
    )


@pytest.mark.parametrize("count", ["-1", "true", '"1"'])
def test_omitted_finding_counts_are_nonnegative_integers(count: str) -> None:
    report = (
        f'{{"reportVersion":0,"status":"failed","failureKind":"quality","omittedFindings":{count}}}'
    )
    with pytest.raises(AdapterError):
        adapt_native(context(), report.encode())


def test_combination_preserves_prior_and_newly_omitted_findings() -> None:
    findings = [
        {"id": f"error-{index}", "severity": "error", "message": "bad"} for index in range(6_000)
    ]
    report = {
        "reportVersion": 0,
        "status": "failed",
        "failureKind": "quality",
        "findings": findings,
        "omittedFindings": 3,
    }
    combined = combine([("first", report), ("second", report)])
    assert len(combined["findings"]) == 10_000
    assert combined["omittedFindings"] == 2_006


def test_incomplete_passing_producer_cannot_establish_success() -> None:
    result = adapt_native(context(), b'{"reportVersion":0,"status":"passed","omittedFindings":1}')
    assert result.status is ResultStatus.FAILED


def test_incomplete_passing_report_cannot_hide_command_failure() -> None:
    result = adapt_native(
        context(succeeded=False),
        b'{"reportVersion":0,"status":"passed","omittedFindings":1}',
    )
    assert result.failure_kind is FailureKind.COMMAND


def test_sarif_retains_full_counts_when_findings_are_bounded() -> None:
    report = {
        "runs": [
            {
                "results": [
                    {"level": "error", "message": {"text": f"failure {index}"}}
                    for index in range(10_001)
                ]
            }
        ]
    }
    result = adapt_sarif(context(), json.dumps(report).encode())
    assert len(result.findings) == 10_000
    assert result.omitted_findings == 1
    assert result.metrics[0].value == "10001"
    assert result.status is ResultStatus.FAILED
