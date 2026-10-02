import pytest

from quality_graph_core.adapters import AdapterError, adapt_junit, adapt_native
from quality_graph_core.graph import Graph
from quality_graph_core.policy import ApprovalTarget, effective_graph
from quality_graph_core.result import ControlKind, Result, ResultStatus
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
