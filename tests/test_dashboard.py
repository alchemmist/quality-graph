from pathlib import Path

import pytest

from qg_github.dashboard import (
    DashboardControlGroup,
    DashboardModel,
    DashboardRun,
    aggregate_status,
    dashboard_metric,
    final_dashboard,
    load_results,
    pending_dashboard,
    render_dashboard,
)
from quality_graph_core.graph import Graph
from quality_graph_core.result import (
    Control,
    ControlKind,
    FailureKind,
    Metric,
    Provenance,
    Result,
    ResultStatus,
)
from tests.test_graph import GRAPH


def result(
    node_id: str,
    status: ResultStatus,
    *,
    metrics: tuple[Metric, ...] = (),
    controls: tuple[Control, ...] = (),
) -> Result:
    return Result(
        node_id,
        node_id.title(),
        status,
        Provenance("owner/repository", "a" * 40, 1, 1, "b" * 64, 42),
        FailureKind.QUALITY if status is ResultStatus.FAILED else None,
        metrics=metrics,
        controls=controls,
    )


def test_result_loader_selects_newest_attempt_for_every_node(tmp_path: Path) -> None:
    old = tmp_path / "quality-result-lint-1"
    new = tmp_path / "quality-result-lint-2"
    newest = tmp_path / "quality-result-lint-10"
    direct = tmp_path / "format.json"
    old.mkdir()
    new.mkdir()
    newest.mkdir()
    (old / "lint.json").write_text(result("lint", ResultStatus.FAILED).to_json())
    (new / "lint.json").write_text(result("lint", ResultStatus.PASSED).to_json())
    (newest / "lint.json").write_text(result("lint", ResultStatus.PASSED).to_json())
    direct.write_text(result("format", ResultStatus.PASSED).to_json())

    loaded = load_results(tmp_path)

    assert loaded["lint"].status is ResultStatus.PASSED
    assert loaded["format"].status is ResultStatus.PASSED
    assert load_results(tmp_path / "missing") == {}


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((ResultStatus.PASSED,), ResultStatus.PASSED),
        ((ResultStatus.FAILED,), ResultStatus.FAILED),
        ((ResultStatus.CANCELLED,), ResultStatus.FAILED),
        ((ResultStatus.FAILED, ResultStatus.WAITING), ResultStatus.IN_PROGRESS),
        ((ResultStatus.PASSED, ResultStatus.IN_PROGRESS), ResultStatus.IN_PROGRESS),
    ],
)
def test_aggregate_status_applies_lifecycle_precedence(
    statuses: tuple[ResultStatus, ...], expected: ResultStatus
) -> None:
    assert aggregate_status(statuses) is expected


def test_final_and_pending_dashboard_keep_declaration_order() -> None:
    graph = Graph.from_yaml(GRAPH)
    lint = result(
        "lint",
        ResultStatus.FAILED,
        metrics=(Metric("Findings|total", "2\nitems"), Metric("Errors", "1"), Metric("Extra", "3")),
        controls=(Control(ControlKind.FINDING, "finding", checked=True),),
    )

    final = final_dashboard(
        graph,
        {"lint": lint},
        DashboardRun(10, 2, "a" * 40, "https://example.test/run/10"),
    )
    pending = pending_dashboard(
        graph,
        DashboardRun(11, 1, "b" * 40, "https://example.test/run/11"),
    )

    assert [row.node_id for row in final.rows] == ["format", "lint"]
    assert final.rows[0].status is ResultStatus.SKIPPED
    assert final.rows[1].metric == "Findings&#124;total: 2<br>items · Errors: 1"
    assert final.control_groups[0].controls[0].checked is True
    assert all(row.status is ResultStatus.WAITING for row in pending.rows)
    assert "## 🚀 Quality Graph" in render_dashboard(pending)


def test_dashboard_renderer_outputs_status_metrics_links_and_controls() -> None:
    graph = Graph.from_yaml(GRAPH)
    lint = result(
        "lint",
        ResultStatus.FAILED,
        controls=(Control(ControlKind.FINDING, "finding", checked=True),),
    )
    model = final_dashboard(
        graph,
        {"lint": lint},
        DashboardRun(10, 2, "a" * 40, "https://example.test/run/10"),
    )

    rendered = render_dashboard(model)

    assert "## ❌ Quality Graph" in rendered
    assert "| Lint | ❌ failed |" in rendered
    assert "[Summary](https://example.test/run/10#quality-graph-lint)" in rendered
    assert "- [x] finding: `finding`" in rendered
    assert "Run `10` attempt `2`" in rendered


def test_dashboard_renderer_bounds_controls_with_summary_links() -> None:
    controls = tuple(
        Control(ControlKind.FILE, f"src/file-{index}-{'x' * 3_900}.py") for index in range(50)
    )
    model = DashboardModel(
        ResultStatus.FAILED,
        "Failure",
        1,
        1,
        "a" * 40,
        (),
        (
            DashboardControlGroup("empty", "Empty", ()),
            DashboardControlGroup("lint", "Lint", controls),
        ),
    )

    rendered = render_dashboard(model)

    assert len(rendered) < 65_536
    assert "additional actions are available in the Job Summary" in rendered


def test_dashboard_metric_has_empty_placeholder() -> None:
    assert dashboard_metric(None) == "—"
    assert dashboard_metric(result("lint", ResultStatus.PASSED)) == "—"
