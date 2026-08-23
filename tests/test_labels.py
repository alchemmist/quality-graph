import pytest

from qg_github.github import MemoryGitHubPort
from qg_github.labels import (
    configured_label_names,
    label_state_marker,
    parse_label_state,
    reconcile_labels,
)
from quality_graph_core.graph import Graph
from quality_graph_core.result import FailureKind, Provenance, Result, ResultStatus
from tests.test_graph import GRAPH


def graph() -> Graph:
    return Graph.from_yaml(GRAPH)


def result(status: ResultStatus) -> Result:
    return Result(
        "lint",
        "Lint",
        status,
        Provenance("owner/repository", "a" * 40, 1, 1, "b" * 64, 42),
        FailureKind.QUALITY if status is ResultStatus.FAILED else None,
    )


def labels_path() -> str:
    return "/issues/42/labels?per_page=100&page=1"


def test_label_state_marker_round_trips_owned_names() -> None:
    names = {"quality:failed", "quality:lint"}
    body = label_state_marker(names)

    assert parse_label_state(body) == names
    assert parse_label_state("ordinary") == frozenset()
    assert parse_label_state("<!-- quality-graph:labels:invalid -->") == frozenset()
    invalid = label_state_marker(())
    encoded_list = invalid.replace("W10", "WzFd")
    assert parse_label_state(encoded_list) == frozenset()


def test_disabled_labels_make_no_github_requests() -> None:
    disabled = Graph.from_yaml(
        GRAPH.replace("enabled: true\n  failing: quality:failed", "enabled: false")
    )
    port = MemoryGitHubPort()

    reconcile_labels(port, 42, disabled, {})

    assert configured_label_names(disabled) == ()
    assert port.requests == []


def test_label_reconciliation_adds_creates_and_removes_owned_labels() -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", labels_path(), [{"name": "quality:old"}, {"name": "unrelated"}])
    port.enqueue("POST", "/issues/42/labels", [{"name": "quality:failed"}])
    port.enqueue("GET", "/labels/quality%3Alint", None)
    port.enqueue("POST", "/labels", {"id": 1})
    port.enqueue("POST", "/issues/42/labels", [{"name": "quality:lint"}])
    port.enqueue("DELETE", "/issues/42/labels/quality%3Aold", None)

    reconcile_labels(
        port,
        42,
        graph(),
        {"lint": result(ResultStatus.FAILED)},
        previous_owned={"quality:old"},
    )

    paths = [request[1] for request in port.requests]
    assert "/labels" in paths
    assert paths.count("/issues/42/labels") == 2
    assert "/issues/42/labels/quality%3Aold" in paths
    assert all("unrelated" not in path for path in paths)


def test_label_reconciliation_removes_recovered_current_labels() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        labels_path(),
        [{"name": "quality:failed"}, {"name": "quality:lint"}],
    )
    port.enqueue("DELETE", "/issues/42/labels/quality%3Afailed", None)
    port.enqueue("DELETE", "/issues/42/labels/quality%3Alint", None)

    reconcile_labels(port, 42, graph(), {"lint": result(ResultStatus.PASSED)})

    assert [request[0] for request in port.requests].count("DELETE") == 2


def test_label_reconciliation_reuses_existing_repository_label() -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", labels_path(), [{"name": "quality:failed"}])
    port.enqueue("GET", "/labels/quality%3Alint", {"name": "quality:lint"})
    port.enqueue("POST", "/issues/42/labels", [{"name": "quality:lint"}])

    reconcile_labels(port, 42, graph(), {"lint": result(ResultStatus.FAILED)})

    assert all(request[1] != "/labels" for request in port.requests)


def test_label_reconciliation_rejects_invalid_github_label_shape() -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", labels_path(), [{"name": 1}])
    with pytest.raises(TypeError, match="pull request label"):
        reconcile_labels(port, 42, graph(), {})
