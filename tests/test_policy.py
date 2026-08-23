import pytest

from quality_graph.graph import Graph
from quality_graph.policy import ApprovalTarget, effective_graph
from quality_graph.result import (
    ControlKind,
    FailureKind,
    Finding,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)
from tests.test_graph import GRAPH


def result(*, failure: FailureKind = FailureKind.QUALITY) -> Result:
    return Result(
        "lint",
        "Lint",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 1, 1, "b" * 64, 42),
        failure,
        findings=(
            Finding(
                "error",
                Severity.ERROR,
                "Error",
                location=SourceLocation("src/app.py", 1, 1),
            ),
            Finding(
                "warning",
                Severity.WARNING,
                "Warning",
                location=SourceLocation("src/app.py", 2, 2),
            ),
        ),
    )


def test_approval_target_round_trips_stable_ledger_key() -> None:
    target = ApprovalTarget(ControlKind.FINDING, "finding")

    assert ApprovalTarget.from_key(target.key) == target
    with pytest.raises(ValueError, match="invalid approval target"):
        ApprovalTarget.from_key("invalid")
    with pytest.raises(ValueError, match="ControlKind"):
        ApprovalTarget.from_key("unknown:file")


def test_effective_graph_exposes_policy_controls_and_discards_stale_approvals() -> None:
    graph = Graph.from_yaml(GRAPH)
    finding = ApprovalTarget(ControlKind.FINDING, "error")
    file = ApprovalTarget(ControlKind.FILE, "src/app.py")
    stale = ApprovalTarget(ControlKind.FINDING, "removed")

    effective = effective_graph(graph, {"lint": result()}, {finding, file, stale})

    assert effective.targets == frozenset(
        {
            ApprovalTarget(ControlKind.FINDING, "error"),
            ApprovalTarget(ControlKind.FINDING, "warning"),
            file,
        }
    )
    assert stale not in effective.approvals
    assert effective.results["lint"].status is ResultStatus.PASSED
    checked = {control.target: control.checked for control in effective.results["lint"].controls}
    assert checked == {"error": True, "warning": False, "src/app.py": True}


def test_finding_approval_only_removes_its_own_blocking_failure() -> None:
    graph = Graph.from_yaml(GRAPH)
    approval = ApprovalTarget(ControlKind.FINDING, "error")

    effective = effective_graph(graph, {"lint": result()}, {approval})

    assert effective.results["lint"].status is ResultStatus.FAILED


def test_non_quality_failure_cannot_be_approved() -> None:
    graph = Graph.from_yaml(GRAPH)
    approval = ApprovalTarget(ControlKind.FILE, "src/app.py")

    effective = effective_graph(
        graph,
        {"lint": result(failure=FailureKind.ADAPTER)},
        {approval},
    )

    assert effective.results["lint"].failure_kind is FailureKind.ADAPTER
    assert effective.results["lint"].controls[0].checked is False


def test_node_approval_handles_quality_failure_without_findings() -> None:
    graph = Graph.from_yaml(GRAPH.replace("files: true", "files: true\n        node: true"))
    failed = Result(
        "lint",
        "Lint",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 1, 1, "b" * 64, 42),
        FailureKind.QUALITY,
    )
    approval = ApprovalTarget(ControlKind.NODE, "lint")

    effective = effective_graph(graph, {"lint": failed}, {approval})

    assert effective.results["lint"].status is ResultStatus.PASSED
    assert effective.results["lint"].notes[-1].startswith("The effective result passes")


def test_missing_results_produce_no_targets_or_effective_results() -> None:
    effective = effective_graph(Graph.from_yaml(GRAPH), {}, set())

    assert effective.results == {}
    assert effective.targets == frozenset()


def test_node_can_disable_individual_finding_controls() -> None:
    graph = Graph.from_yaml(GRAPH.replace("files: true", "findings: false\n        files: true"))

    effective = effective_graph(graph, {"lint": result()}, set())

    assert effective.targets == frozenset({ApprovalTarget(ControlKind.FILE, "src/app.py")})
