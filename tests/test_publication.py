import base64
import hashlib
import io
import json
import zipfile

import pytest

from qg_github.comments import marker, upsert_managed_comment
from qg_github.compiler import compile_graph
from qg_github.github import MemoryGitHubPort
from qg_github.publication import (
    DashboardNode,
    DashboardRun,
    PublicationOutcome,
    WorkflowRunEvent,
    _workflow_job_status,
    _workflow_jobs,
    publish_workflow_jobs,
    publish_workflow_run,
    read_event_json,
    watch_workflow_run,
)
from quality_graph_core.graph import Graph
from quality_graph_core.result import JsonValue, Provenance, Result, ResultStatus
from tests.test_graph import GRAPH, NONE_PROJECTION_GRAPH

RUNS_PATH = "/actions/workflows/quality-graph.yml/runs?event=pull_request&per_page=100&page=1"
JOBS_PATH = "/actions/runs/10/jobs?filter=latest&per_page=100&page=1"


def event(action: str = "in_progress", *, pull: bool = True) -> dict[str, JsonValue]:
    pulls: list[JsonValue] = []
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


def configure_publication(
    port: MemoryGitHubPort,
    *,
    comment_id: int = 5,
    source: str = GRAPH,
    check_runs: list[JsonValue] | None = None,
) -> None:
    port.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}},
    )
    port.enqueue("GET", RUNS_PATH, {"workflow_runs": []})
    content = base64.b64encode(source.encode()).decode()
    port.enqueue("GET", f"/contents/qg.yaml?ref={'d' * 40}", {"content": content})
    comments = "/issues/42/comments?per_page=100&page=1"
    port.enqueue("GET", comments, [])
    port.enqueue(
        "POST",
        "/issues/42/comments",
        {"id": comment_id, "body": marker("dashboard") + "\nbody"},
    )
    checks_path = (
        f"/commits/{'a' * 40}/check-runs?check_name=Quality%20Graph&filter=all&per_page=100"
    )
    port.enqueue("GET", checks_path, {"check_runs": check_runs or []})
    port.enqueue("POST", "/check-runs", {"id": 100})
    port.enqueue("GET", "/issues/42/labels?per_page=100&page=1", [])


def result_archive(
    node_id: str,
    title: str,
    *,
    graph_digest: str | None = None,
    source: str = GRAPH,
) -> bytes:
    digest = graph_digest or compile_graph(Graph.from_yaml(source)).graph_digest
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


def test_final_publisher_ignores_non_completed_events() -> None:
    port = MemoryGitHubPort()

    outcome = publish_workflow_run(port, event())

    assert outcome == PublicationOutcome(published=False)
    assert port.requests == []


def test_job_coordinator_merges_parallel_job_lifecycle_without_lost_updates() -> None:
    port = MemoryGitHubPort()
    nodes = (DashboardNode("format", "Formatting"), DashboardNode("lint", "Lint"))
    port.enqueue(
        "GET",
        JOBS_PATH,
        {
            "total_count": 2,
            "jobs": [
                {"name": "Formatting", "status": "in_progress"},
                {"name": "Lint", "status": "queued"},
            ],
        },
        {
            "total_count": 2,
            "jobs": [
                {"name": "Formatting", "status": "completed", "conclusion": "success"},
                {"name": "Lint", "status": "in_progress"},
            ],
        },
    )
    comments = "/issues/42/comments?per_page=100&page=1"
    port.enqueue("GET", comments, [])
    port.enqueue("POST", "/issues/42/comments", {"id": 5, "body": marker("dashboard")})
    port.enqueue(
        "GET",
        comments,
        [{"id": 5, "body": marker("dashboard"), "user": {"login": "github-actions[bot]"}}],
    )
    port.enqueue("PATCH", "/issues/comments/5", {"id": 5, "body": marker("dashboard")})
    run = DashboardRun(10, 1, "a" * 40, "https://example.test/run/10")

    assert publish_workflow_jobs(port, 42, nodes, run) is False
    assert publish_workflow_jobs(port, 42, nodes, run) is False

    writes = [request[2]["body"] for request in port.requests if request[0] in {"POST", "PATCH"}]
    assert "| Formatting | 🚀 in_progress |" in writes[0]
    assert "| Lint | ⏳ waiting |" in writes[0]
    assert "| Formatting | ✅ passed |" in writes[1]
    assert "| Lint | 🚀 in_progress |" in writes[1]


def test_requested_event_finalizes_after_every_node_is_terminal() -> None:
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
    port.enqueue(
        "GET",
        JOBS_PATH,
        {
            "total_count": 2,
            "jobs": [
                {"name": "Formatting", "status": "completed", "conclusion": "success"},
                {"name": "Lint", "status": "completed", "conclusion": "success"},
            ],
        },
    )

    outcome = watch_workflow_run(port, event("requested"), sleep=lambda _: None)

    assert outcome == PublicationOutcome(
        published=True,
        status=ResultStatus.PASSED,
        comment_id=5,
    )
    check = [request for request in port.requests if request[1] == "/check-runs"][-1]
    assert check[2]["status"] == "completed"
    assert check[2]["conclusion"] == "success"


def test_requested_event_polls_until_jobs_finish() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    port.enqueue(
        "GET",
        JOBS_PATH,
        {
            "jobs": [
                {"name": "Formatting", "status": "in_progress"},
                {"name": "Lint", "status": "queued"},
            ]
        },
        {
            "jobs": [
                {"name": "Formatting", "status": "completed", "conclusion": "success"},
                {"name": "Lint", "status": "completed", "conclusion": "success"},
            ]
        },
    )
    comments = "/issues/42/comments?per_page=100&page=1"
    existing = {
        "id": 5,
        "body": marker("dashboard"),
        "user": {"login": "github-actions[bot]"},
    }
    port.enqueue("GET", comments, [existing])
    port.enqueue("PATCH", "/issues/comments/5", {"id": 5, "body": marker("dashboard")})
    port.enqueue("GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": []})
    sleeps: list[float] = []

    watch_workflow_run(port, event("requested"), sleep=sleeps.append)

    assert sleeps == [30.0]


def test_requested_event_does_not_finalize_after_becoming_superseded() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    port.enqueue(
        "GET",
        RUNS_PATH,
        {"workflow_runs": [{"id": 11, "pull_requests": [{"number": 42}]}]},
    )
    port.enqueue(
        "GET",
        JOBS_PATH,
        {"jobs": [{"name": "Formatting", "status": "in_progress"}]},
    )

    outcome = watch_workflow_run(port, event("requested"), sleep=lambda _: None)

    assert outcome == PublicationOutcome(published=False)
    assert all("comments" not in request[1] for request in port.requests)


def test_requested_event_rejects_a_newer_attempt_of_the_same_run() -> None:
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
                {
                    "id": 10,
                    "run_attempt": 2,
                    "pull_requests": [{"number": 42}],
                }
            ]
        },
    )

    outcome = watch_workflow_run(port, event("requested"), sleep=lambda _: None)

    assert outcome == PublicationOutcome(published=False)
    assert len(port.requests) == 2


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        ({"status": "queued"}, ResultStatus.WAITING),
        ({"status": "pending"}, ResultStatus.IN_PROGRESS),
        ({"status": "requested"}, ResultStatus.IN_PROGRESS),
        ({"status": "waiting"}, ResultStatus.IN_PROGRESS),
        ({"status": "completed", "conclusion": "success"}, ResultStatus.PASSED),
        ({"status": "completed", "conclusion": "skipped"}, ResultStatus.SKIPPED),
        ({"status": "completed", "conclusion": "cancelled"}, ResultStatus.CANCELLED),
        ({"status": "completed", "conclusion": "failure"}, ResultStatus.FAILED),
    ],
)
def test_workflow_job_status_maps_github_lifecycle(
    job: dict[str, JsonValue], expected: ResultStatus
) -> None:
    assert _workflow_job_status(job) is expected


def test_workflow_job_status_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="unsupported workflow job status"):
        _workflow_job_status({"status": "unknown"})


def test_workflow_jobs_paginates_complete_job_list() -> None:
    port = MemoryGitHubPort()
    first = "/actions/runs/10/jobs?filter=latest&per_page=100&page=1"
    second = "/actions/runs/10/jobs?filter=latest&per_page=100&page=2"
    jobs = [{"name": f"job-{index}", "status": "queued"} for index in range(100)]
    port.enqueue("GET", first, {"jobs": jobs})
    port.enqueue("GET", second, {"jobs": [{"name": "last", "status": "queued"}]})

    assert len(_workflow_jobs(port, 10)) == 101


def test_job_coordinator_ignores_non_graph_jobs() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        JOBS_PATH,
        {"jobs": [{"name": "publisher", "status": "in_progress"}]},
    )
    comments = "/issues/42/comments?per_page=100&page=1"
    port.enqueue("GET", comments, [])
    port.enqueue("POST", "/issues/42/comments", {"id": 5, "body": marker("dashboard")})

    assert (
        publish_workflow_jobs(
            port,
            42,
            (DashboardNode("format", "Formatting"),),
            DashboardRun(10, 1, "a" * 40, "https://example.test/run/10"),
        )
        is False
    )


def test_job_coordinator_does_not_overwrite_terminal_or_superseded_state() -> None:
    terminal = MemoryGitHubPort()
    terminal.enqueue(
        "GET",
        JOBS_PATH,
        {"jobs": [{"name": "Formatting", "status": "completed", "conclusion": "success"}]},
    )
    node = (DashboardNode("format", "Formatting"),)
    run = DashboardRun(10, 1, "a" * 40, "https://example.test/run/10")

    assert publish_workflow_jobs(terminal, 42, node, run) is True
    assert all("comments" not in request[1] for request in terminal.requests)

    interleaved = MemoryGitHubPort()
    interleaved.enqueue(
        "GET",
        JOBS_PATH,
        {"jobs": [{"name": "Formatting", "status": "in_progress"}]},
    )
    comments = "/issues/42/comments?per_page=100&page=1"
    existing = {
        "id": 5,
        "body": marker("dashboard") + "\nold",
        "user": {"login": "github-actions[bot]"},
    }
    interleaved.enqueue("GET", comments, [existing])
    interleaved.enqueue("PATCH", "/issues/comments/5", {"id": 5, "body": "final"})

    def final_published() -> bool:
        upsert_managed_comment(interleaved, 42, "dashboard", "FINAL")
        return False

    assert (
        publish_workflow_jobs(
            interleaved,
            42,
            node,
            run,
            is_current=final_published,
        )
        is True
    )
    writes = [request for request in interleaved.requests if request[0] == "PATCH"]
    assert len(writes) == 1
    assert "FINAL" in writes[0][2]["body"]
    assert "in_progress" not in writes[0][2]["body"]


@pytest.mark.parametrize(
    "event_value",
    [
        event("completed"),
        event("requested") | {"workflow_run": {**event()["workflow_run"], "event": "push"}},
    ],
)
def test_watcher_ignores_non_requested_pull_runs(event_value: dict[str, JsonValue]) -> None:
    port = MemoryGitHubPort()

    assert watch_workflow_run(port, event_value).published is False
    assert port.requests == []


def test_watcher_rejects_unassociated_stale_and_superseded_runs() -> None:
    unassociated = MemoryGitHubPort()
    unassociated.enqueue("GET", f"/commits/{'c' * 40}/pulls", [])
    assert watch_workflow_run(unassociated, event("requested", pull=False)).published is False

    stale = MemoryGitHubPort()
    stale.enqueue(
        "GET",
        "/pulls/42",
        {"head": {"sha": "b" * 40}, "base": {"sha": "d" * 40}},
    )
    assert watch_workflow_run(stale, event("requested")).published is False

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
    assert watch_workflow_run(superseded, event("requested")).published is False


def test_job_coordinator_preserves_previous_owned_labels() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        JOBS_PATH,
        {"jobs": [{"name": "Formatting", "status": "in_progress"}]},
    )
    comments = "/issues/42/comments?per_page=100&page=1"
    existing = {
        "id": 5,
        "body": "<!-- quality-graph:dashboard -->\n<!-- quality-graph:labels:WyJvbGQiXQ -->",
        "user": {"login": "github-actions[bot]"},
    }
    port.enqueue("GET", comments, [existing])
    port.enqueue("PATCH", "/issues/comments/5", {"id": 5, "body": "updated"})

    publish_workflow_jobs(
        port,
        42,
        (DashboardNode("format", "Formatting"),),
        DashboardRun(10, 1, "a" * 40, "https://example.test/run/10"),
    )

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


def test_repeated_completed_publication_updates_its_existing_check_run() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    external_id = "quality-graph:10:1"
    configure_publication(port, check_runs=[{"id": 100, "external_id": external_id}])
    format_archive = result_archive("format", "Formatting")
    lint_archive = result_archive("lint", "Lint")
    artifacts_path = "/actions/runs/10/artifacts?per_page=100&page=1"
    artifacts = {
        "artifacts": [
            artifact(1, "format", format_archive),
            artifact(2, "lint", lint_archive),
        ]
    }
    port.enqueue("GET", artifacts_path, artifacts, artifacts)
    port.downloads.update(
        {
            "/actions/artifacts/1/zip": format_archive,
            "/actions/artifacts/2/zip": lint_archive,
        }
    )
    port.enqueue("PATCH", "/check-runs/100", {"id": 100})

    publish_workflow_run(port, event("completed"))
    publish_workflow_run(port, event("completed"))

    writes = [request for request in port.requests if request[1] == "/check-runs"]
    updates = [request for request in port.requests if request[1] == "/check-runs/100"]
    assert len(writes) == 1
    assert writes[0][2]["external_id"] == external_id
    assert len(updates) == 1


def test_completed_event_accepts_none_projection_with_excluded_dependency() -> None:
    port = MemoryGitHubPort()
    configure_publication(port, source=NONE_PROJECTION_GRAPH)
    lint_archive = result_archive("lint", "Lint", source=NONE_PROJECTION_GRAPH)
    port.enqueue(
        "GET",
        "/actions/runs/10/artifacts?per_page=100&page=1",
        {"artifacts": [artifact(1, "lint", lint_archive)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = lint_archive

    outcome = publish_workflow_run(port, event("completed"))

    assert outcome.status is ResultStatus.PASSED


def test_watcher_accepts_none_projection_with_excluded_dependency() -> None:
    port = MemoryGitHubPort()
    configure_publication(port, source=NONE_PROJECTION_GRAPH)
    lint_archive = result_archive("lint", "Lint", source=NONE_PROJECTION_GRAPH)
    port.enqueue(
        "GET",
        "/actions/runs/10/artifacts?per_page=100&page=1",
        {"artifacts": [artifact(1, "lint", lint_archive)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = lint_archive
    port.enqueue(
        "GET",
        JOBS_PATH,
        {"jobs": [{"name": "Lint", "status": "completed", "conclusion": "success"}]},
    )

    outcome = watch_workflow_run(port, event("requested"), sleep=lambda _: None)

    assert outcome.status is ResultStatus.PASSED


def test_completed_event_surfaces_invalid_artifacts_as_failure() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    port.enqueue("GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": []})

    outcome = publish_workflow_run(port, event("completed"))

    assert outcome.status is ResultStatus.FAILED
    check = next(request for request in port.requests if request[1] == "/check-runs")
    assert check[2]["conclusion"] == "failure"


def test_completed_event_preserves_job_statuses_when_artifact_provenance_is_stale() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    stale = result_archive("format", "Formatting", graph_digest="f" * 64)
    port.enqueue(
        "GET",
        "/actions/runs/10/artifacts?per_page=100&page=1",
        {"artifacts": [artifact(1, "format", stale)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = stale
    port.enqueue(
        "GET",
        JOBS_PATH,
        {
            "jobs": [
                {"name": "Formatting", "status": "completed", "conclusion": "success"},
                {"name": "Lint", "status": "completed", "conclusion": "failure"},
            ]
        },
    )

    outcome = publish_workflow_run(port, event("completed"))

    assert outcome.status is ResultStatus.FAILED
    comment = next(
        request for request in port.requests if request[0] == "POST" and "comments" in request[1]
    )
    assert "final dashboard could not be assembled" in comment[2]["body"]
    assert "| Formatting | ✅ passed |" in comment[2]["body"]
    assert "| Lint | ❌ failed |" in comment[2]["body"]


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
    assert publish_workflow_run(stale_head, event("completed")).published is False

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
    assert publish_workflow_run(superseded, event("completed")).published is False


def test_publisher_ignores_non_pull_request_workflow_runs() -> None:
    value = event()
    value["workflow_run"]["event"] = "push"

    assert publish_workflow_run(MemoryGitHubPort(), value).published is False


def test_publisher_resolves_pull_from_commit_and_handles_no_association() -> None:
    missing = MemoryGitHubPort()
    missing.enqueue("GET", f"/commits/{'c' * 40}/pulls", [])
    assert publish_workflow_run(missing, event("completed", pull=False)).published is False

    missing_number = MemoryGitHubPort()
    missing_number.enqueue("GET", f"/commits/{'c' * 40}/pulls", [{}])
    assert publish_workflow_run(missing_number, event("completed", pull=False)).published is False

    resolved = MemoryGitHubPort()
    resolved.enqueue("GET", f"/commits/{'c' * 40}/pulls", [{"number": 41}, {"number": 42}])
    configure_publication(resolved)
    resolved.enqueue("GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": []})
    assert publish_workflow_run(resolved, event("completed", pull=False)).published is True


def test_publisher_rejects_unknown_action_and_invalid_event_values() -> None:
    port = MemoryGitHubPort()
    configure_publication(port)
    assert publish_workflow_run(port, event("cancelled")).published is False

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
    port.enqueue("GET", f"/contents/qg.yaml?ref={'d' * 40}", {"content": "%%%"})

    with pytest.raises(ValueError, match="base64"):
        publish_workflow_run(port, event("completed"))
