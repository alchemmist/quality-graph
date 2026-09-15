from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_cli.cli import main
from qg_cli.project import Project
from qg_cli.providers import load_provider
from qg_github.compiler import compile_graph as compile_github
from qg_gitlab.compiler import compile_graph, graph_digest
from qg_gitlab.compiler import starter as unconfigured_starter
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from pathlib import Path

    from quality_graph_core.result import JsonValue


def starter(branch: str) -> str:
    source = yaml.safe_load(unconfigured_starter(branch))
    source["provider"]["configuration"]["publisher-user-id"] = 42
    return yaml.safe_dump(source)


def test_gitlab_provider_discovery_and_onboarding(tmp_path: Path) -> None:
    assert load_provider("gitlab").name == "gitlab"
    assert main(["init", "--provider", "gitlab", "--root", str(tmp_path)]) == 0
    (tmp_path / "qg.yaml").write_text(starter("main"))
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
    assert workflow["qg-internal:admission:mr"]["needs"] == []
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default-branch", 'bad"branch'),
        ("runtime-version", "0.0.0"),
        ("runner-tags", []),
        ("runner-tags", [1]),
        ("publisher-tags", "tag"),
        ("ci-path", "outside.yml"),
    ],
)
def test_gitlab_rejects_unsupported_configuration_values(field: str, value: JsonValue) -> None:
    source = yaml.safe_load(starter("main"))
    source["provider"]["configuration"][field] = value
    with pytest.raises(ValueError, match=r"GitLab|runner-tags|publisher-tags"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runner", "windows-latest"),
        ("permissions", {"contents": "write"}),
        ("services", {"db": {"image": "postgres"}}),
    ],
)
def test_gitlab_rejects_incompatible_execution_profiles(field: str, value: JsonValue) -> None:
    source = yaml.safe_load(starter("main"))
    source["profiles"]["default"][field] = value
    with pytest.raises(ValueError, match=r"GitLab|GitHub"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


@pytest.mark.parametrize(
    ("field", "value"),
    [("shell", "pwsh"), ("permissions", {"contents": "read"}), ("environment", "production")],
)
def test_gitlab_rejects_non_quality_job_escape_hatches(field: str, value: JsonValue) -> None:
    source = yaml.safe_load(starter("main"))
    source["nodes"]["quality"][field] = value
    with pytest.raises(ValueError, match="GitLab"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_gitlab_profiles_apply_environment_timeout_and_nonblocking_policy() -> None:
    source = yaml.safe_load(starter("main"))
    source["provider"]["configuration"]["api-url"] = "https://api.example.test/gitlab/v4"
    source["profiles"]["default"].update(
        {"env": {"BASE": "base", "VALUE": "profile"}, "timeout-minutes": 10}
    )
    source["profiles"]["default"].pop("container")
    source["nodes"]["quality"].update({"env": {"VALUE": "node"}, "policy": {"blocking": False}})
    workflow = yaml.safe_load(
        compile_graph(Graph.from_yaml(yaml.safe_dump(source))).files[0].content
    )
    job = workflow["qg:mr:quality"]
    assert job["variables"] == {"BASE": "base", "VALUE": "node"}
    assert job["timeout"] == "10 minutes"
    assert job["allow_failure"] is True


def test_gitlab_rejects_other_providers() -> None:
    graph = Graph.from_yaml(starter("main"))
    with pytest.raises(ValueError, match="cannot compile provider"):
        compile_graph(replace(graph, provider=replace(graph.provider, name="other")))


def flow_source() -> dict[str, JsonValue]:
    source = yaml.safe_load(starter("main"))
    source["operations"] = source.pop("nodes")
    source["flows"] = {
        "main": {"trigger": {"push": {"branches": ["main"]}}, "nodes": {"quality": {}}}
    }
    return source


def test_gitlab_preserves_release_plans_without_executing_them() -> None:
    source = flow_source()
    flows = source["flows"]
    assert isinstance(flows, dict)
    flows["release"] = {
        "trigger": {"push": {"tags": ["v*"]}},
        "concurrency": "release",
        "nodes": {"quality": {}},
    }
    assert len(compile_graph(Graph.from_yaml(yaml.safe_dump(source))).files) == 3
    flows["release"]["execution"] = "github-actions"
    with pytest.raises(ValueError, match="executable release flows"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_gitlab_rejects_duplicate_events_and_foreign_presentation() -> None:
    source = flow_source()
    source["flows"] = {
        "first": {"trigger": "pull-request", "nodes": {"quality": {}}},
        "second": {"trigger": "pull-request", "nodes": {"quality": {}}},
    }
    with pytest.raises(ValueError, match="at most one"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))
    source["flows"] = {
        "first": {"trigger": "pull-request", "presentation": "github-pr", "nodes": {"quality": {}}}
    }
    with pytest.raises(ValueError, match="presentation"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_user_admission_node_does_not_replace_the_internal_gate() -> None:
    source = yaml.safe_load(starter("main"))
    source["nodes"] = {"admission": {"run": "true"}}
    workflow = yaml.safe_load(
        compile_graph(Graph.from_yaml(yaml.safe_dump(source))).files[0].content
    )
    assert "qg:mr:admission" in workflow
    assert "qg-internal:admission:mr" in workflow


def test_github_rejects_gitlab_presentation() -> None:
    source = flow_source()
    source["provider"] = {
        "name": "github",
        "configuration": {
            "default-branch": "main",
            "runtime": {"action": "owner/runtime@" + "a" * 40},
        },
    }
    source["flows"] = {
        "review": {"trigger": "pull-request", "presentation": "gitlab-mr", "nodes": {"quality": {}}}
    }
    with pytest.raises(ValueError, match="GitHub cannot use GitLab"):
        compile_github(Graph.from_yaml(yaml.safe_dump(source)))


@pytest.mark.parametrize(
    ("field", "value"), [("concurrency", "custom"), ("execution", "github-actions")]
)
def test_gitlab_rejects_ignored_flow_overrides(field: str, value: str) -> None:
    source = flow_source()
    flows = source["flows"]
    assert isinstance(flows, dict)
    flow = flows["main"]
    assert isinstance(flow, dict)
    flow[field] = value
    with pytest.raises(ValueError, match=r"overrides|release flows"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_gitlab_does_not_generate_an_empty_executable_pipeline() -> None:
    source = yaml.safe_load(starter("main"))
    source["nodes"] = {}
    with pytest.raises(ValueError, match="at least one executable"):
        compile_graph(Graph.from_yaml(yaml.safe_dump(source)))


def test_mr_publication_requires_publisher_identity_during_generation() -> None:
    with pytest.raises(ValueError, match="requires a declared publisher-user-id"):
        compile_graph(Graph.from_yaml(unconfigured_starter("main")))


def test_push_only_generation_does_not_require_publisher_identity() -> None:
    source = flow_source()
    del source["provider"]["configuration"]["publisher-user-id"]
    assert compile_graph(Graph.from_yaml(yaml.safe_dump(source))).files


def test_mr_without_publication_does_not_require_publisher_identity() -> None:
    source = flow_source()
    del source["provider"]["configuration"]["publisher-user-id"]
    source["flows"] = {
        "review": {"trigger": "pull-request", "presentation": "none", "nodes": {"quality": {}}}
    }
    workflow = yaml.safe_load(
        compile_graph(Graph.from_yaml(yaml.safe_dump(source))).files[0].content
    )
    assert "qg:review:quality" in workflow
    assert "qg-internal:admission:review" not in workflow
