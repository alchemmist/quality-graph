from dataclasses import replace

import pytest

from qg_github.compiler import pr_contract, project_graph
from quality_graph_core.graph import Graph
from tests.test_flow_compiler import SOURCE


def test_pr_contract_keeps_checks_and_governance_but_excludes_pin_revisions() -> None:
    graph = Graph.from_yaml(SOURCE)
    contract = pr_contract(project_graph(graph, "pull-request"))
    assert set(contract) == {
        "provider",
        "configuration",
        "runtimeRepository",
        "nodes",
        "profiles",
        "labels",
        "administration",
    }
    assert contract["provider"] == "github"
    assert contract["configuration"] == {}
    assert contract["runtimeRepository"] == "owner/runtime"
    assert contract["administration"] == ["admin"]
    assert contract["labels"] == {"enabled": False}
    assert contract["profiles"] == {
        "default": {
            "runner": "ubuntu-latest",
            "setup": [],
            "env": {},
            "permissions": {"contents": "read"},
            "services": {},
        }
    }
    assert len(contract["nodes"]) == 2
    moved_pin = replace(
        graph,
        provider=replace(
            graph.provider,
            values={
                "runtime": {"action": "owner/runtime@" + "2" * 40},
            },
        ),
    )
    assert pr_contract(project_graph(moved_pin, "pull-request")) == contract
    other_runner = replace(graph, profiles=(replace(graph.profiles[0], runner="self-hosted"),))
    assert pr_contract(project_graph(other_runner, "pull-request")) != contract


def test_pr_contract_rejects_an_unpinned_runtime() -> None:
    graph = Graph.from_yaml(SOURCE)
    graph = replace(
        graph,
        provider=replace(graph.provider, values={"runtime": {"action": "owner/runtime@main"}}),
    )
    with pytest.raises(
        ValueError, match="GitHub runtime action must use owner/repository@40-character-commit"
    ):
        pr_contract(project_graph(graph, "pull-request"))
