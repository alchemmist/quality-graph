import hashlib
import io
import zipfile

import pytest

from quality_graph.commands import handle_command
from quality_graph.compiler import compile_graph
from quality_graph.github import HttpGitHubPort
from quality_graph.graph import Graph
from quality_graph.publication import publish_workflow_run
from quality_graph.result import (
    FailureKind,
    Finding,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)
from tests.integration.fake_github import FakeGitHubServer, FakeGitHubState
from tests.test_graph import GRAPH

pytestmark = pytest.mark.integration


def archive(result: Result) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(f"{result.node_id}.json", result.to_json())
    return output.getvalue()


def state() -> FakeGitHubState:
    digest = compile_graph(Graph.from_yaml(GRAPH)).graph_digest
    format_result = Result(
        "format",
        "Formatting",
        ResultStatus.PASSED,
        Provenance("owner/repository", "a" * 40, 10, 1, digest, 42),
    )
    lint_result = Result(
        "lint",
        "Lint",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 10, 1, digest, 42),
        FailureKind.QUALITY,
        findings=(
            Finding(
                "finding",
                Severity.ERROR,
                "Failure",
                location=SourceLocation("src/app.py", 1, 1),
            ),
        ),
    )
    downloads = {1: archive(format_result), 2: archive(lint_result)}
    artifacts = [
        {
            "id": artifact_id,
            "name": f"quality-result-{node_id}-1",
            "size_in_bytes": len(downloads[artifact_id]),
            "digest": f"sha256:{hashlib.sha256(downloads[artifact_id]).hexdigest()}",
            "expired": False,
        }
        for artifact_id, node_id in ((1, "format"), (2, "lint"))
    ]
    return FakeGitHubState(GRAPH, artifacts, downloads)


def workflow_event() -> dict[str, object]:
    return {
        "action": "completed",
        "workflow_run": {
            "id": 10,
            "run_attempt": 1,
            "head_sha": "c" * 40,
            "html_url": "https://example.test/run/10",
            "pull_requests": [{"number": 42, "head": {"sha": "a" * 40}}],
        },
    }


def command_event() -> dict[str, object]:
    return {
        "action": "created",
        "issue": {"number": 42, "pull_request": {}},
        "comment": {
            "id": 10,
            "body": "/qg ignore-file src/app.py",
            "user": {"login": "admin"},
        },
        "sender": {"login": "admin"},
    }


def test_full_publication_and_command_lifecycle_over_real_http() -> None:
    server = FakeGitHubServer(state())
    port = HttpGitHubPort(
        "owner/repository",
        "token",
        base_url=server.base_url,
    )

    with server as observable:
        publication = publish_workflow_run(port, workflow_event())
        command = handle_command(port, command_event())

    assert publication.status is ResultStatus.FAILED
    assert command.authorized is True
    assert command.changed is True
    assert len(observable.comments) == 2
    assert "## ❌ Quality Graph" in observable.comments[0]["body"]
    assert "quality-graph:approval" in observable.comments[1]["body"]
    assert observable.checks[-1]["conclusion"] == "failure"
    assert observable.labels == {"quality:failed", "quality:lint"}
    assert observable.reruns == [10]
    assert observable.reactions == [(10, "eyes"), (10, "hooray")]
