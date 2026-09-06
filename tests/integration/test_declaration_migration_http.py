from __future__ import annotations

import base64
from typing import TYPE_CHECKING, cast

import pytest
import yaml

from qg_github.artifacts import ArtifactError, ArtifactExpectation
from qg_github.compiler import compile_graph, project_graph
from qg_github.declarations import read_pr_results
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run
from quality_graph_core.graph import Graph
from quality_graph_core.result import JsonValue, Provenance, Result, ResultStatus
from tests.integration.test_artifacts_http import archive, metadata
from tests.integration.test_github_lifecycle_http import workflow_event
from tests.test_graph import GRAPH

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def migrated_declaration() -> dict[str, JsonValue]:
    source = cast("dict[str, JsonValue]", yaml.safe_load(GRAPH))
    nodes = cast("dict[str, dict[str, JsonValue]]", source.pop("nodes"))
    placements: dict[str, JsonValue] = {}
    for name, node in nodes.items():
        placements[name] = {"needs": node.pop("needs", [])}
    source["operations"] = cast("JsonValue", nodes)
    source["flows"] = {
        "review": {"trigger": "pull-request", "presentation": "github-pr", "nodes": placements}
    }
    return source


def configure_migration(
    fake_github: FakeGitHubScenario,
    source: str,
    *,
    head_content: str | None = None,
    failures: tuple[JsonValue, ...] = (),
) -> HttpGitHubPort:
    compiled = compile_graph(Graph.from_yaml(source))
    artifacts: list[JsonValue] = []
    downloads: dict[str, JsonValue] = {}
    for identifier, (node_id, title) in enumerate((("format", "Formatting"), ("lint", "Lint")), 1):
        content = archive(
            Result(
                node_id,
                title,
                ResultStatus.PASSED,
                Provenance(
                    "owner/repository",
                    "a" * 40,
                    10,
                    1,
                    compiled.graph_digest,
                    42,
                    "review",
                    node_id,
                ),
            )
        )
        artifacts.append(metadata(identifier, f"quality-result-{node_id}-1", content))
        downloads[str(identifier)] = base64.b64encode(content).decode()
    fake_github.reset(
        {
            "contents": {
                f"{'d' * 40}:qg.yaml": GRAPH,
                f"{'a' * 40}:qg.yaml": source if head_content is None else head_content,
            },
            "failures": list(failures),
            "run_artifacts": {"10": artifacts},
            "downloads": downloads,
        }
    )
    return HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)


def test_equivalent_flow_migration_publishes_without_forging_base_provenance(
    fake_github: FakeGitHubScenario,
) -> None:
    source = yaml.safe_dump(migrated_declaration())
    port = configure_migration(fake_github, source)

    outcome = publish_workflow_run(port, workflow_event())

    assert outcome.status is ResultStatus.PASSED
    assert len(cast("list[JsonValue]", fake_github.snapshot()["checks"])) == 1


@pytest.mark.parametrize(
    "change",
    [
        "command",
        "dependencies",
        "policy",
        "roles",
        "runtime-repository",
        "presentation",
        "upload-repository",
    ],
)
def test_changed_pr_contract_cannot_use_migration_to_bypass_base_policy(
    fake_github: FakeGitHubScenario, change: str
) -> None:
    source = migrated_declaration()
    operations = cast("dict[str, dict[str, JsonValue]]", source["operations"])
    if change == "command":
        operations["lint"]["run"] = "true"
    elif change == "dependencies":
        flows = cast("dict[str, dict[str, JsonValue]]", source["flows"])
        cast("dict[str, JsonValue]", flows["review"]["nodes"])["lint"] = {}
    elif change == "policy":
        operations["lint"]["policy"] = {"blocking": False}
    elif change == "roles":
        source["administration"] = {"roles": ["write"]}
    elif change == "presentation":
        cast("dict[str, dict[str, JsonValue]]", source["flows"])["review"]["presentation"] = "none"
    elif change == "upload-repository":
        provider = cast("dict[str, dict[str, JsonValue]]", source["provider"])
        cast("dict[str, JsonValue]", provider["configuration"]["runtime"])[
            "upload-artifact-action"
        ] = "other/uploader@" + "1" * 40
    else:
        provider = cast("dict[str, dict[str, JsonValue]]", source["provider"])
        provider["configuration"]["runtime"] = {"action": "other/runtime@" + "1" * 40}
    port = configure_migration(fake_github, yaml.safe_dump(source))

    outcome = publish_workflow_run(port, workflow_event())

    assert outcome.status is ResultStatus.FAILED


@pytest.mark.parametrize("head_content", [GRAPH, "not a declaration", "x" * 1_048_577])
def test_invalid_head_content_cannot_justify_a_new_digest(
    fake_github: FakeGitHubScenario, head_content: str
) -> None:
    port = configure_migration(
        fake_github, yaml.safe_dump(migrated_declaration()), head_content=head_content
    )
    assert publish_workflow_run(port, workflow_event()).status is ResultStatus.FAILED


@pytest.mark.parametrize("status", [200, 204, 500])
def test_migration_fails_closed_on_invalid_github_content_responses(
    fake_github: FakeGitHubScenario, status: int
) -> None:
    source = yaml.safe_dump(migrated_declaration())
    port = configure_migration(
        fake_github,
        source,
        failures=(
            {
                "method": "GET",
                "path": "/repos/owner/repository/contents/qg.yaml",
                "status": status,
            },
        ),
    )
    graph = Graph.from_yaml(GRAPH)
    expectation = ArtifactExpectation(
        "owner/repository",
        42,
        "a" * 40,
        10,
        compile_graph(graph).graph_digest,
        frozenset({"format", "lint"}),
    )
    with pytest.raises(ArtifactError, match="provenance"):
        read_pr_results(port, project_graph(graph, "pull-request"), expectation)
