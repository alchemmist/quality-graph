import base64
import hashlib
import io
import json
import zipfile

import pytest

from qg_github.comments import marker
from qg_github.compiler import compile_graph
from qg_github.github import MemoryGitHubPort
from qg_github.publication import (
    PublicationOutcome,
    WorkflowRunEvent,
    publish_workflow_run,
    read_event_json,
)
from quality_graph_core.graph import Graph
from quality_graph_core.result import Provenance, Result, ResultStatus
from tests.test_graph import GRAPH

RUNS_PATH = "/actions/workflows/quality-graph.yml/runs?event=pull_request&per_page=100&page=1"


def event(action: str = "in_progress", *, pull: bool = True) -> dict[str, object]:
    pulls: list[object] = []
    if pull:
        pulls.append({"number": 42, "head": {"sha": "a" * 40}})
    return {
        "action": action,
        "workflow_run": {
            "event": "pull_request",
            "id": 10,
            "run_attempt": 1,
            "head_sha": "c" * 40,
            "html_url": "https://example.test/run/10",
            "pull_requests": pulls,
        },
    }


def configure_publication(port: MemoryGitHubPort, *, comment_id: int = 5) -> None:
    port.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    port.enqueue("GET", RUNS_PATH, {"workflow_runs": []})
    content = base64.b64encode(GRAPH.encode()).decode()
    port.enqueue("GET", f"/contents/quality-graph.yml?ref={'d' * 40}", {"content": content})
    comments = "/issues/42/comments?per_page=100&page=1"
    port.enqueue("GET", comments, [])
    port.enqueue(
        "POST",
        "/issues/42/comments",
        {"id": comment_id, "body": marker("dashboard") + "\nbody"},
    )
    port.enqueue("POST", "/check-runs", {"id": 100})
    port.enqueue("GET", "/issues/42/labels?per_page=100&page=1", [])


def result_archive(node_id: str, title: str) -> bytes:
    digest = compile_graph(Graph.from_yaml(GRAPH)).graph_digest
    result = Result(
        node_id,
        title,
        ResultStatus.PASSED,
        Provenance("owner/repository", "a" * 40, 10, 1, digest, 42),
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(f"{node_id}.json", result.to_json())
    return output.getvalue()


def artifact(artifact_id: int, node_id: str, archive: bytes) -> dict[str, object]:
    return {
        "id": artifact_id,
        "name": f"quality-result-{node_id}-1",
        "size_in_bytes": len(archive),
        "digest": f"sha256:{hashlib.sha256(archive).hexdigest()}",
        "expired": False,
    }


def test_in_progress_event_publishes_pending_dashboard_and_check() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)

    outcome = publish_workflow_run(port, event())

    assert outcome == PublicationOutcome(
        published=True,
        status=ResultStatus.IN_PROGRESS,
        comment_id=5,
    )
    comment_request = next(
        request for request in port.requests if request[0] == "POST" and "comments" in request[1]
    )
    assert "## 🚀 Quality Graph" in comment_request[2]["body"]
    assert "| Formatting | 🚀 in_progress |" in comment_request[2]["body"]
    assert "| Lint | ⏳ waiting |" in comment_request[2]["body"]
    check = port.requests[-1]
    assert check[1] == "/check-runs"
    assert check[2]["status"] == "in_progress"
    assert "conclusion" not in check[2]


def test_in_progress_event_preserves_previous_owned_labels() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    port.enqueue("GET", RUNS_PATH, {"workflow_runs": []})
    content = base64.b64encode(GRAPH.encode()).decode()
    port.enqueue(
        "GET",
        f"/contents/quality-graph.yml?ref={'d' * 40}",
        {"content": content},
    )
    comments = "/issues/42/comments?per_page=100&page=1"
    existing = {
        "id": 5,
        "body": "<!-- quality-graph:dashboard -->\n<!-- quality-graph:labels:WyJvbGQiXQ -->",
        "user": {"login": "github-actions[bot]"},
    }
    port.enqueue("GET", comments, [existing])
    port.enqueue("PATCH", "/issues/comments/5", {"id": 5, "body": "updated"})
    port.enqueue("POST", "/check-runs", {"id": 100})

    outcome = publish_workflow_run(port, event())

    assert outcome.published is True
    patch = next(request for request in port.requests if request[0] == "PATCH")
    assert "WyJvbGQiXQ" in patch[2]["body"]


def test_completed_event_downloads_results_and_publishes_success() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    format_archive = result_archive("format", "Formatting")
    lint_archive = result_archive("lint", "Lint")
    artifacts_path = "/actions/runs/10/artifacts?per_page=100&page=1"
    port.enqueue(
        "GET",
        artifacts_path,
        {
            "artifacts": [
                artifact(1, "format", format_archive),
                artifact(2, "lint", lint_archive),
            ]
        },
    )
    port.downloads.update(
        {
            "/actions/artifacts/1/zip": format_archive,
            "/actions/artifacts/2/zip": lint_archive,
        }
    )

    outcome = publish_workflow_run(port, event("completed"))

    assert outcome.status is ResultStatus.PASSED
    check = next(request for request in port.requests if request[1] == "/check-runs")
    assert check[2]["conclusion"] == "success"


def test_completed_event_surfaces_invalid_artifacts_as_failure() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    port.enqueue("GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": []})

    outcome = publish_workflow_run(port, event("completed"))

    assert outcome.status is ResultStatus.FAILED
    check = next(request for request in port.requests if request[1] == "/check-runs")
    assert check[2]["conclusion"] == "failure"


def test_completed_event_preserves_partial_results_when_dependencies_skip() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    format_archive = result_archive("format", "Formatting")
    artifacts_path = "/actions/runs/10/artifacts?per_page=100&page=1"
    port.enqueue(
        "GET",
        artifacts_path,
        {"artifacts": [artifact(1, "format", format_archive)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = format_archive

    outcome = publish_workflow_run(port, event("completed"))

    assert outcome.status is ResultStatus.FAILED
    comment = next(
        request for request in port.requests if request[0] == "POST" and "comments" in request[1]
    )
    assert "| Formatting | ✅ passed |" in comment[2]["body"]
    assert "| Lint | ⏭️ skipped |" in comment[2]["body"]


def test_publisher_rejects_stale_head_and_superseded_run() -> None:
    stale_head = MemoryGitHubPort()
    stale_head.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "e" * 40}, "base": {"sha": "d" * 40}},
    )
    assert publish_workflow_run(stale_head, event()).published is False

    superseded = MemoryGitHubPort()
    superseded.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    superseded.enqueue(
        "GET",
        RUNS_PATH,
        {"workflow_runs": [{"id": 11, "pull_requests": [{"number": 42}]}]},
    )
    assert publish_workflow_run(superseded, event()).published is False


def test_publisher_ignores_non_pull_request_workflow_runs() -> None:
    value = event()
    value["workflow_run"]["event"] = "push"

    assert publish_workflow_run(MemoryGitHubPort(), value).published is False


def test_publisher_resolves_pull_from_commit_and_handles_no_association() -> None:
    missing = MemoryGitHubPort()
    missing.enqueue("GET", f"/commits/{'c' * 40}/pulls", [])
    assert publish_workflow_run(missing, event(pull=False)).published is False

    missing_number = MemoryGitHubPort()
    missing_number.enqueue("GET", f"/commits/{'c' * 40}/pulls", [{}])
    assert publish_workflow_run(missing_number, event(pull=False)).published is False

    resolved = MemoryGitHubPort()
    resolved.enqueue("GET", f"/commits/{'c' * 40}/pulls", [{"number": 41}, {"number": 42}])
    configure_publication(resolved)
    assert publish_workflow_run(resolved, event(pull=False)).published is True


def test_publisher_rejects_unknown_action_and_invalid_event_values() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    with pytest.raises(ValueError, match="unsupported"):
        publish_workflow_run(port, event("cancelled"))

    with pytest.raises(TypeError, match="GitHub event"):
        read_event_json("[]")
    with pytest.raises(json.JSONDecodeError):
        read_event_json("invalid")


def test_workflow_event_narrows_optional_pull_head() -> None:
    value = event()
    value["workflow_run"]["pull_requests"][0]["head"] = "invalid"

    parsed = WorkflowRunEvent.from_value(value)

    assert parsed.pull_request == 42
    assert parsed.pull_head_sha is None


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"action": "started", "workflow_run": []},
        {
            "action": "started",
            "workflow_run": {
                "id": 1,
                "event": "pull_request",
                "head_sha": "a",
                "html_url": "url",
                "pull_requests": {},
            },
        },
        {
            "action": 1,
            "workflow_run": {
                "id": 1,
                "event": "pull_request",
                "head_sha": "a",
                "html_url": "url",
                "pull_requests": [],
            },
        },
        {
            "action": "started",
            "workflow_run": {
                "id": False,
                "event": "pull_request",
                "head_sha": "a",
                "html_url": "url",
                "pull_requests": [],
            },
        },
    ],
)
def test_workflow_event_rejects_invalid_shapes(value: object) -> None:
    with pytest.raises(TypeError):
        WorkflowRunEvent.from_value(value)


def test_publisher_rejects_invalid_base_configuration_encoding() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    port.enqueue(
        "GET",
        RUNS_PATH,
        {
            "workflow_runs": [
                {"pull_requests": [{"number": 41}]},
                {"pull_requests": [{"number": 42}]},
            ]
        },
    )
    port.enqueue("GET", f"/contents/quality-graph.yml?ref={'d' * 40}", {"content": "%%%"})

    with pytest.raises(ValueError, match="base64"):
        publish_workflow_run(port, event())
