from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_cli.cli import main
from qg_cli.project import Project
from qg_cli.providers import load_provider
from qg_gitlab.compiler import compile_graph, graph_digest, starter
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from pathlib import Path


def test_gitlab_provider_discovery_and_onboarding(tmp_path: Path) -> None:
    assert load_provider("gitlab").name == "gitlab"
    assert main(["init", "--provider", "gitlab", "--root", str(tmp_path)]) == 0
    assert main(["generate", "--root", str(tmp_path)]) == 0
    assert main(["validate", "--root", str(tmp_path)]) == 0
    project = Project.open(tmp_path)
    generated = project.render()
    assert len(generated.files) == 3
    assert project.graph.provider.name == "gitlab"
    assert "actions/checkout" not in (tmp_path / ".gitlab-ci.yml").read_text()


def test_gitlab_compilation_is_deterministic_and_preserves_native_dependencies() -> None:
    source = yaml.safe_load(starter("main"))
    source["nodes"] = {
        "lint": {"run": "make lint"},
        "test": {"run": "make test", "needs": ["lint"]},
    }
    graph = Graph.from_yaml(yaml.safe_dump(source))
    generated = compile_graph(graph)
    assert generated == compile_graph(graph)
    workflow = yaml.safe_load(generated.files[0].content)
    assert workflow["qg:mr:test"]["needs"] == ["qg:mr:lint"]
    assert workflow["qg:push:test"]["needs"] == ["qg:push:lint"]
    assert workflow["qg:mr:test"]["artifacts"]["when"] == "always"
    assert workflow["qg:mr:admission"]["needs"] == []
    assert "make test" not in generated.files[0].content


def test_gitlab_digest_changes_with_command_and_policy() -> None:
    graph = Graph.from_yaml(starter("main"))
    node = graph.nodes[0]
    changed_command = replace(
        graph, nodes=(replace(node, step=replace(node.step, run="make test")),)
    )
    changed_policy = replace(
        graph, nodes=(replace(node, policy=replace(node.policy, blocking=False)),)
    )
    assert (
        len({graph_digest(graph), graph_digest(changed_command), graph_digest(changed_policy)}) == 3
    )


def test_gitlab_publisher_is_protected_and_does_not_check_out_consumer_code() -> None:
    generated = compile_graph(Graph.from_yaml(starter("main")))
    publisher = yaml.safe_load(generated.files[2].content)
    assert 'CI_COMMIT_REF_PROTECTED == "true"' in publisher["workflow"]["rules"][0]["if"]
    assert publisher["publish"]["variables"]["GIT_STRATEGY"] == "none"
    assert publisher["publish"]["script"] == ["qg-gitlab publish"]


@pytest.mark.parametrize("field", ["action", "runtime", "unknown"])
def test_gitlab_rejects_unknown_provider_configuration(field: str) -> None:
    source = yaml.safe_load(starter("main"))
    source["provider"]["configuration"][field] = "x"
    with pytest.raises(ValueError, match="unknown GitLab"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_gitlab_rejects_github_action_steps() -> None:
    source = yaml.safe_load(starter("main"))
    source["nodes"]["quality"] = {"uses": "actions/checkout@v7"}
    with pytest.raises(ValueError, match="not GitHub Actions"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_gitlab_explicit_flows_keep_operation_placement_and_branch_filters() -> None:
    source = yaml.safe_load(starter("main"))
    source["operations"] = source.pop("nodes")
    source["flows"] = {
        "review": {
            "trigger": "pull-request",
            "presentation": "gitlab-mr",
            "nodes": {"verify": {"operation": "quality"}},
        },
        "main": {
            "trigger": {"push": {"branches": ["main"]}},
            "nodes": {"verify": {"operation": "quality"}},
        },
    }
    compiled = compile_graph(Graph.from_yaml(yaml.safe_dump(source)))
    workflow = yaml.safe_load(compiled.files[0].content)
    assert "qg:review:verify" in workflow
    assert '$CI_COMMIT_BRANCH == "main"' in workflow["qg:main:verify"]["rules"][0]["if"]
