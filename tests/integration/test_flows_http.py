from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest

from qg_github.artifacts import ArtifactError, download_results
from qg_github.commands import dispatch_pr_command, handle_command
from qg_github.compiler import compile_graph
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run, watch_workflow_run
from qg_github.runtime import CollectionRequest, collect, publish_collection
from quality_graph_core.graph import Graph
from quality_graph_core.result import JsonValue, ResultStatus
from tests.integration.test_artifacts_http import archive, expectation, metadata, result
from tests.integration.test_commands_http import event as command_event
from tests.integration.test_github_lifecycle_http import workflow_event
from tests.test_flow_compiler import SOURCE

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def test_explicit_pr_collection_and_publication_converge_over_http(
    fake_github: FakeGitHubScenario,
    tmp_path: Path,
) -> None:
    compiled = compile_graph(Graph.from_yaml(SOURCE))
    artifacts: list[JsonValue] = []
    downloads: dict[str, JsonValue] = {}
    for identifier, node_id in enumerate(("lint", "test"), 1):
        request = CollectionRequest.from_environment(
            {
                "QG_ADAPTER": "exit-code",
                "QG_NODE_ID": node_id,
                "QG_TITLE": node_id.title(),
                "GITHUB_WORKSPACE": str(tmp_path),
                "RUNNER_TEMP": str(tmp_path),
                "GITHUB_OUTPUT": str(tmp_path / "output"),
                "GITHUB_REPOSITORY": "owner/repository",
                "GITHUB_SHA": "a" * 40,
                "GITHUB_RUN_ID": "10",
                "GITHUB_RUN_ATTEMPT": "1",
                "QG_GRAPH_DIGEST": compiled.graph_digest,
                "QG_FLOW_ID": "review",
                "QG_OPERATION_ID": node_id,
                "QG_PRESENTATION": "github-pr",
                "QG_APPROVAL_FINDINGS": "true",
                "QG_APPROVAL_FILES": "false",
                "QG_APPROVAL_NODE": "false",
            },
            {"pull_request": {"number": 42}},
        )
        collected = collect(request)
        assert publish_collection(request, collected) == 0
        content = archive(collected)
        artifacts.append(metadata(identifier, f"quality-result-{node_id}-1", content))
        downloads[str(identifier)] = base64.b64encode(content).decode()
    fake_github.reset(
        {
            "contents": {f"{'d' * 40}:qg.yaml": SOURCE},
            "run_artifacts": {"10": artifacts},
            "downloads": downloads,
        }
    )
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    first = publish_workflow_run(port, workflow_event())
    second = publish_workflow_run(port, workflow_event())
    observed = fake_github.snapshot()

    assert first.status is ResultStatus.PASSED
    assert second.status is ResultStatus.PASSED
    assert len(cast("list[JsonValue]", observed["comments"])) == 1
    assert len(cast("list[JsonValue]", observed["checks"])) == 1


@pytest.mark.parametrize("flow_id", ["main", "release", None])
def test_pr_artifact_reader_rejects_a_different_or_missing_flow(
    fake_github: FakeGitHubScenario,
    flow_id: str | None,
) -> None:
    original = result()
    content = archive(
        replace(
            original,
            provenance=replace(
                original.provenance,
                flow_id=flow_id,
                operation_id="lint" if flow_id else None,
            ),
        )
    )
    fake_github.reset(
        {
            "run_artifacts": {"10": [metadata(1, "quality-result-lint-1", content)]},
            "downloads": {"1": base64.b64encode(content).decode()},
        }
    )
    with pytest.raises(ArtifactError, match="provenance"):
        download_results(
            HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url),
            replace(expectation(), flow_id="review", operation_ids={"lint": "lint"}),
        )


@pytest.mark.parametrize("action", ["completed", "requested"])
def test_disabled_pr_adapter_makes_no_repository_writes(
    fake_github: FakeGitHubScenario,
    action: str,
) -> None:
    fake_github.reset(
        {
            "contents": {
                f"{'d' * 40}:qg.yaml": SOURCE.replace(
                    "presentation: github-pr", "presentation: none"
                ),
            }
        }
    )
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    event = workflow_event() | {"action": action}
    outcome = (
        publish_workflow_run(port, event)
        if action == "completed"
        else watch_workflow_run(port, event)
    )
    observed = fake_github.snapshot()

    assert outcome.published is False
    assert observed["comments"] == []
    assert observed["checks"] == []
    assert observed["labels"] == []


@pytest.mark.parametrize("enabled", [True, False])
def test_pr_command_adapter_respects_trusted_flow_presentation(
    fake_github: FakeGitHubScenario,
    *,
    enabled: bool,
) -> None:
    source = SOURCE if enabled else SOURCE.replace("presentation: github-pr", "presentation: none")
    fake_github.reset({"contents": {f"{'d' * 40}:qg.yaml": source}})
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    outcome = dispatch_pr_command(port, command_event("/qg help"))
    observed = fake_github.snapshot()

    assert outcome.handled is enabled
    assert len(cast("list[JsonValue]", observed["comments"])) == int(enabled)
    if not enabled:
        with pytest.raises(ValueError, match="presentation is disabled"):
            handle_command(port, command_event("/qg ignore lint"))
    assert not dispatch_pr_command(port, command_event("hello")).handled
