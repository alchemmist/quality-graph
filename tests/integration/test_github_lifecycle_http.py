import hashlib
import io
import zipfile
from typing import cast

import pytest

from qg_github.commands import handle_command
from qg_github.compiler import compile_graph
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run, watch_workflow_run
from quality_graph_core.graph import Graph
from quality_graph_core.result import (
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


def state(source: str = GRAPH) -> FakeGitHubState:
    digest = compile_graph(Graph.from_yaml(source)).graph_digest
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
    return FakeGitHubState(source, artifacts, downloads)


def workflow_event() -> dict[str, object]:
    return {
        "action": "completed",
        "workflow_run": {
            "event": "pull_request",
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
        observed = observable.snapshot()

    assert publication.status is ResultStatus.FAILED
    assert command.authorized is True
    assert command.changed is True
    comments = cast("list[dict[str, object]]", observed["comments"])
    checks = cast("list[dict[str, object]]", observed["checks"])
    labels = cast("list[dict[str, object]]", observed["labels"])
    reactions = cast("list[dict[str, object]]", observed["reactions"])
    assert len(comments) == 2
    assert "## ❌ Quality Graph" in cast("str", comments[0]["body"])
    assert "quality-graph:approval" in cast("str", comments[1]["body"])
    assert checks[-1]["conclusion"] == "failure"
    assert {label["name"] for label in labels} == {"quality:lint"}
    assert observed["issue_labels"] == {"42": ["quality:failed", "quality:lint"]}
    assert observed["reruns"] == [10]
    assert [reaction["content"] for reaction in reactions] == ["eyes", "hooray"]


def test_live_and_completed_events_finalize_one_check_over_real_http() -> None:
    selected = state()
    selected.job_snapshots.extend(
        [
            [
                {"name": "Formatting", "status": "in_progress"},
                {"name": "Lint", "status": "queued"},
            ],
            [
                {"name": "Formatting", "status": "completed", "conclusion": "success"},
                {"name": "Lint", "status": "completed", "conclusion": "failure"},
            ],
        ]
    )
    server = FakeGitHubServer(selected)
    port = HttpGitHubPort(
        "owner/repository",
        "token",
        base_url=server.base_url,
    )

    with server as observable:
        live = watch_workflow_run(
            port,
            workflow_event() | {"action": "requested"},
            sleep=lambda _: None,
        )
        completed = publish_workflow_run(port, workflow_event())
        observed = observable.snapshot()

    assert live.status is ResultStatus.FAILED
    assert completed.status is ResultStatus.FAILED
    checks = cast("list[dict[str, object]]", observed["checks"])
    comments = cast("list[dict[str, object]]", observed["comments"])
    assert len(checks) == 1
    assert checks[0]["status"] == "completed"
    assert checks[0]["conclusion"] == "failure"
    assert "waiting" not in cast("str", comments[0]["body"])


def test_live_watcher_avoids_noop_patches_and_stays_within_request_budget() -> None:
    selected = state()
    selected.job_snapshots.extend(
        [
            [
                {"name": "Formatting", "status": "in_progress"},
                {"name": "Lint", "status": "queued"},
            ],
            [
                {"name": "Formatting", "status": "in_progress"},
                {"name": "Lint", "status": "queued"},
            ],
            [
                {"name": "Formatting", "status": "completed", "conclusion": "success"},
                {"name": "Lint", "status": "completed", "conclusion": "failure"},
            ],
        ]
    )
    server = FakeGitHubServer(selected)
    port = HttpGitHubPort("owner/repository", "token", base_url=server.base_url)

    with server as observable:
        outcome = watch_workflow_run(
            port,
            workflow_event() | {"action": "requested"},
            sleep=lambda _: None,
        )
        observed = observable.snapshot()

    assert outcome.status is ResultStatus.FAILED
    requests = cast("list[dict[str, object]]", observed["requests"])
    job_reads = [request for request in requests if "/actions/runs/10/jobs" in str(request["path"])]
    comment_patches = [
        request
        for request in requests
        if request["method"] == "PATCH" and "/issues/comments/" in str(request["path"])
    ]
    assert len(job_reads) == 3
    assert len(comment_patches) == 1


def test_stale_and_superseded_events_cannot_publish_over_http() -> None:
    stale = state()
    stale.pulls[42]["head"] = {"sha": "e" * 40}
    stale_server = FakeGitHubServer(stale)
    stale_port = HttpGitHubPort("owner/repository", "token", base_url=stale_server.base_url)

    with stale_server as observable:
        stale_outcome = publish_workflow_run(stale_port, workflow_event())
        stale_state = observable.snapshot()

    superseded = state()
    superseded.workflow_runs = [{"id": 11, "run_attempt": 1, "pull_requests": [{"number": 42}]}]
    superseded_server = FakeGitHubServer(superseded)
    superseded_port = HttpGitHubPort(
        "owner/repository",
        "token",
        base_url=superseded_server.base_url,
    )

    with superseded_server as observable:
        superseded_outcome = publish_workflow_run(superseded_port, workflow_event())
        superseded_state = observable.snapshot()

    assert stale_outcome.published is False
    assert superseded_outcome.published is False
    assert stale_state["comments"] == stale_state["checks"] == []
    assert superseded_state["comments"] == superseded_state["checks"] == []


def test_invalid_artifacts_publish_terminal_failure_from_job_state_over_http() -> None:
    selected = state()
    selected.run_artifacts[10] = [
        {
            "id": 1,
            "name": "quality-result-lint-1",
            "size_in_bytes": 0,
            "digest": "sha256:" + "0" * 64,
            "expired": True,
        }
    ]
    selected.workflow_jobs[10] = [
        {"name": "Formatting", "status": "completed", "conclusion": "success"},
        {"name": "Lint", "status": "completed", "conclusion": "failure"},
    ]
    server = FakeGitHubServer(selected)
    port = HttpGitHubPort("owner/repository", "token", base_url=server.base_url)

    with server as observable:
        outcome = publish_workflow_run(port, workflow_event())
        observed = observable.snapshot()

    assert outcome.status is ResultStatus.FAILED
    comments = cast("list[dict[str, object]]", observed["comments"])
    checks = cast("list[dict[str, object]]", observed["checks"])
    assert "could not be assembled" in cast("str", comments[0]["body"])
    assert "Formatting | ✅ passed" in cast("str", comments[0]["body"])
    assert checks[0]["status"] == "completed"
    assert checks[0]["conclusion"] == "failure"


def test_completed_workflow_finalizes_when_pull_request_renames_graph_nodes() -> None:
    source = GRAPH.replace(
        "labels:\n",
        "  unit:\n"
        "    title: Unit tests\n"
        "    needs: [lint]\n"
        "    run: make test-unit\n"
        "labels:\n",
    )
    selected = state(source)
    selected.workflow_runs = [
        {
            "id": 10,
            "run_attempt": 1,
            "status": "completed",
            "pull_requests": [{"number": 42}],
        }
    ]
    selected.run_artifacts[10] = [
        {
            "id": 1,
            "name": "quality-result-lint-1",
            "size_in_bytes": 0,
            "digest": "sha256:" + "0" * 64,
            "expired": True,
        }
    ]
    selected.workflow_jobs[10] = [
        {"name": "Formatting", "status": "completed", "conclusion": "success"},
        {"name": "Lint", "status": "completed", "conclusion": "failure"},
        {"name": "Fast tests", "status": "completed", "conclusion": "skipped"},
    ]
    server = FakeGitHubServer(selected)
    port = HttpGitHubPort("owner/repository", "token", base_url=server.base_url)

    def unexpected_sleep(_seconds: float) -> None:
        message = "watcher did not observe the completed workflow run"
        raise AssertionError(message)

    with server as observable:
        outcome = watch_workflow_run(
            port,
            workflow_event() | {"action": "requested"},
            sleep=unexpected_sleep,
        )
        observed = observable.snapshot()

    assert outcome.status is ResultStatus.FAILED
    comments = cast("list[dict[str, object]]", observed["comments"])
    checks = cast("list[dict[str, object]]", observed["checks"])
    assert "Lint | ❌ failed" in cast("str", comments[0]["body"])
    assert checks[0]["status"] == "completed"
