from dataclasses import replace
from pathlib import Path

import pytest

from qg_github.runtime import CollectionRequest
from qg_github.runtime import collect as collect_github
from qg_gitlab.runtime import collect as collect_gitlab
from quality_graph_core.collection import collect_report
from quality_graph_core.graph import AdapterKind, Graph, ResultAdapter
from quality_graph_core.result import FailureKind
from tests.test_adapters import context
from tests.test_runtime import environment, event


@pytest.mark.parametrize(
    ("kind", "payload", "failure"),
    [
        (AdapterKind.EXIT_CODE, b"command output", None),
        (AdapterKind.EXIT_CODE, b"\xff", FailureKind.ADAPTER),
        (AdapterKind.NATIVE, b'{"reportVersion":0,"status":"passed"}', None),
        (AdapterKind.SARIF, b'{"runs":[{"results":[]}]}', None),
        (AdapterKind.JUNIT, b'<testsuite><testcase name="ok"/></testsuite>', None),
        (AdapterKind.NATIVE, b"invalid", FailureKind.ADAPTER),
        (AdapterKind.JUNIT, b"invalid", FailureKind.ADAPTER),
    ],
)
def test_provider_collectors_share_report_semantics(
    tmp_path: Path, kind: AdapterKind, payload: bytes, failure: FailureKind | None
) -> None:
    (tmp_path / "report").write_bytes(payload)
    selected = context()
    request = replace(
        CollectionRequest.from_environment(environment(tmp_path), event()),
        context=selected,
        adapter=kind,
        report_path="report",
        presentation="none",
    )
    graph = Graph.from_yaml("version: 0\nprofiles: {default: {}}\nnodes: {lint: {run: lint}}")
    node = replace(graph.nodes[0], result=ResultAdapter(kind, "report"))
    expected = collect_report(selected, kind, tmp_path, "report")
    assert expected.failure_kind is failure
    assert collect_github(request) == expected
    assert collect_gitlab(selected, node, tmp_path) == expected


@pytest.mark.parametrize("path", [None, "missing", "../outside"])
def test_collection_returns_failure_for_missing_or_escaping_reports(
    tmp_path: Path, path: str | None
) -> None:
    result = collect_report(context(), AdapterKind.NATIVE, tmp_path, path)
    assert result.failure_kind is FailureKind.ADAPTER
