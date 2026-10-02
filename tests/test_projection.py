import pytest

from quality_graph_core.graph import Graph
from quality_graph_core.projection import project_event


def test_projection_preserves_dependencies_or_removes_them_explicitly() -> None:
    source = (
        "version: 0\nprofiles: {default: {}}\nnodes:\n"
        "  build: {run: build, events: [push]}\n"
        "  test: {run: test, needs: [build]}\n"
    )
    graph = Graph.from_yaml(source)
    assert project_event(graph, "push").nodes[1].needs == ("build",)
    with pytest.raises(ValueError, match="excludes dependencies"):
        project_event(graph, "pull-request")
    independent = Graph.from_yaml(source + "execution: {pull-request: {dependencies: none}}\n")
    selected = project_event(independent, "pull-request")
    assert [(node.id, node.needs) for node in selected.nodes] == [("test", ())]


def test_projection_keeps_explicit_operation_placement_and_empty_membership() -> None:
    graph = Graph.from_yaml(
        "version: 0\nprofiles: {default: {}}\noperations: {check: {run: check}}\n"
        "flows: {review: {trigger: pull-request, nodes: {lint: {operation: check}}}}\n"
    )
    selected = project_event(graph, "pull-request", flow_id="review")
    assert selected.flow_id == "review"
    assert selected.nodes[0].operation_id == "check"
    with pytest.raises(ValueError, match="expected one flow"):
        project_event(graph, "push")
    legacy = Graph.from_yaml(
        "version: 0\nprofiles: {default: {}}\nnodes: {lint: {run: lint, events: [push]}}\n"
    )
    assert project_event(legacy, "pull-request").nodes == ()
    with pytest.raises(ValueError, match="legacy"):
        project_event(legacy, "push", flow_id="review")
