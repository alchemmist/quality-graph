from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest
import yaml

from quality_graph_core.graph import FlowNode, Graph
from quality_graph_core.result import JsonValue, Provenance
from tests.test_flow_compiler import SOURCE


def test_operation_reuse_keeps_flow_membership_and_dependencies_separate() -> None:
    graph = Graph.from_yaml(
        "version: 0\n"
        "profiles: {default: {}}\n"
        "operations:\n"
        "  lint: {run: make lint}\n"
        "  format: {run: make fmt-check, diff-only: true}\n"
        "flows:\n"
        "  review:\n"
        "    trigger: pull-request\n"
        "    presentation: github-pr\n"
        "    nodes: {format: {}, check: {operation: lint, needs: [format]}}\n"
        "  main:\n"
        "    trigger: {push: {branches: [main]}}\n"
        "    nodes: {lint: {}}\n"
        "  release:\n"
        "    trigger: workflow-dispatch\n"
        "    concurrency: production\n"
        "    presentation: release\n"
        "    nodes: {verify: {operation: lint}}\n"
    )

    assert len(graph.operations) == 2
    review = graph.for_flow("review")
    main = graph.for_flow("main")
    release = graph.for_flow("release")
    assert review.nodes[1].needs == ("format",)
    assert review.nodes[1].step == main.nodes[0].step == release.nodes[0].step
    assert main.nodes[0].needs == ()
    assert [node.id for node in release.nodes] == ["verify"]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("operations", "lint", "events"), ["push"], "cannot own"),
        (("operations", "lint", "needs"), ["test"], "cannot own"),
        (("operations", "lint", "profile"), "missing", "unknown profile"),
        (("operations", "lint", "diff-only"), True, "PR-only"),
        (("flows", "main", "nodes"), {}, "at least one"),
        (("flows", "main", "nodes", "lint", "operation"), "missing", "unknown operation"),
        (("flows", "main", "nodes", "lint", "needs"), ["missing"], "unknown dependencies"),
        (("flows", "main", "nodes", "lint", "needs"), ["test"], "cycle"),
        (("flows", "main", "nodes", "lint", "needs"), ["lint"], "exclude itself"),
        (("flows", "main", "presentation"), "github-pr", "incompatible"),
        (("flows", "review", "presentation"), "release", "incompatible"),
        (("flows", "main", "presentation"), "other", "unsupported presentation"),
        (("flows", "main", "dependencies"), "parallel", "DependencyPolicy"),
        (("flows", "main", "trigger"), "push", "trigger must"),
        (("flows", "main", "trigger"), {}, "exactly one"),
        (("flows", "main", "trigger"), {"push": {"branches": []}}, "nonempty"),
        (("flows", "main", "trigger"), {"push": {"branches": ["main", "main"]}}, "nonempty"),
        (("flows", "main", "trigger"), {"push": {"branches": [""]}}, "nonempty"),
        (
            ("flows", "main", "nodes", "lint", "checkpoint"),
            {"environment": "production"},
            "only supported",
        ),
        (("flows", "release", "concurrency"), "", "exclusive concurrency"),
        (("flows", "release", "dependencies"), "none", "graph dependencies"),
        (("flows", "release", "nodes", "test", "checkpoint"), {"environment": " "}, "not be empty"),
        (
            ("flows", "release", "nodes", "test", "checkpoint"),
            {"environment": "prestable", "observation-seconds": -1},
            "not be negative",
        ),
        (
            ("flows", "release", "nodes", "test", "checkpoint"),
            {"environment": "prestable", "approval": "yes"},
            "boolean",
        ),
        (("flows", "main", "nodes", "lint", "run"), "make other", "unknown fields"),
    ],
)
def test_flow_validation_rejects_ambiguous_or_unsafe_declarations(
    path: tuple[str, ...],
    value: JsonValue,
    message: str,
) -> None:
    declaration = cast("dict[str, JsonValue]", yaml.safe_load(SOURCE))
    target = declaration
    for key in path[:-1]:
        target = cast("dict[str, JsonValue]", target[key])
    target[path[-1]] = value
    with pytest.raises((ValueError, TypeError), match=message):
        Graph.from_yaml(yaml.safe_dump(declaration))


@pytest.mark.parametrize(
    "inputs",
    [
        {"version": {"type": "string", "default": "v1"}},
        {"dry-run": {"type": "boolean", "default": False}},
        {"attempts": {"type": "number", "default": 2}},
        {"lane": {"type": "choice", "options": ["canary", "stable"], "default": "canary"}},
        {"target": {"type": "environment", "required": True}},
    ],
)
def test_release_inputs_preserve_typed_contracts(inputs: dict[str, JsonValue]) -> None:
    source = SOURCE.replace(
        "trigger: workflow-dispatch",
        "trigger: "
        + yaml.safe_dump(
            {"workflow-dispatch": {"inputs": inputs}},
            default_flow_style=True,
            width=1000,
        ).strip(),
    )
    graph = Graph.from_yaml(source)
    assert graph.flows[2].inputs == inputs


@pytest.mark.parametrize(
    "configuration",
    [
        {"type": "object"},
        {"type": "boolean", "default": "false"},
        {"type": "number", "default": True},
        {"type": "string", "default": 3},
        {"type": "choice", "options": []},
        {"type": "choice", "options": ["a", "a"]},
        {"type": "choice", "options": ["a"], "default": "b"},
        {"type": "string", "options": ["a"]},
    ],
)
def test_release_inputs_reject_invalid_types_and_defaults(
    configuration: dict[str, JsonValue],
) -> None:
    source = SOURCE.replace(
        "trigger: workflow-dispatch",
        "trigger: "
        + yaml.safe_dump(
            {"workflow-dispatch": {"inputs": {"version": configuration}}},
            default_flow_style=True,
            width=1000,
        ).strip(),
    )
    with pytest.raises(ValueError, match="input"):
        Graph.from_yaml(source)


def test_programmatic_flows_validate_membership_and_unique_identities() -> None:
    graph = Graph.from_yaml(SOURCE)
    with pytest.raises(ValueError, match="unique"):
        replace(graph, operations=(*graph.operations, graph.operations[0]))
    with pytest.raises(ValueError, match="unique"):
        replace(graph, flows=(*graph.flows, graph.flows[0]))
    with pytest.raises(ValueError, match="unique"):
        replace(
            graph,
            flows=(
                replace(graph.flows[0], nodes=(FlowNode("lint", "lint"), FlowNode("lint", "lint"))),
            ),
        )
    with pytest.raises(ValueError, match="require operations and flows"):
        replace(graph, operations=())
    with pytest.raises(ValueError, match="unknown flow"):
        graph.for_flow("missing")
    with pytest.raises(ValueError, match="unsupported flow trigger"):
        replace(graph, flows=(replace(graph.flows[0], trigger="other"),))


@pytest.mark.parametrize("suffix", ["nodes: {}\n", "execution: {}\n"])
def test_explicit_declarations_reject_mixing_legacy_membership(suffix: str) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        Graph.from_yaml(SOURCE + suffix)


@pytest.mark.parametrize(
    "source",
    [
        "version: 0\nprofiles: {default: {}}\noperations: {}\nflows: {}\n",
        "version: 0\nprofiles: {default: {}}\noperations: {lint: {run: make lint}}\n",
    ],
)
def test_explicit_declaration_requires_nonempty_catalogue_and_flows(source: str) -> None:
    with pytest.raises(ValueError, match="nonempty operations and flows"):
        Graph.from_yaml(source)


@pytest.mark.parametrize(
    ("flow_id", "operation_id"),
    [(None, "lint"), ("review", None), ("Bad", "lint"), ("review", "Bad")],
)
def test_result_requires_valid_paired_flow_and_operation_provenance(
    flow_id: str | None,
    operation_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="provenance"):
        Provenance(
            "owner/repository",
            "a" * 40,
            10,
            1,
            "b" * 64,
            flow_id=flow_id,
            operation_id=operation_id,
        )
