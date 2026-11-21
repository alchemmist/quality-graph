from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from qg_github.github import MemoryGitHubPort
from qg_github.reconciliation import reconcile_open_pulls
from qg_github.runtime import main
from tests.test_flow_compiler import SOURCE
from tests.test_graph import GRAPH
from tests.test_publication import JOBS_PATH, artifact, configure_publication, result_archive

if TYPE_CHECKING:
    from pathlib import Path

    from quality_graph_core.result import JsonValue

HEAD = "a" * 40
BASE = "d" * 40
PULL = {"state": "open", "number": 42, "head": {"sha": HEAD}, "base": {"sha": BASE, "ref": "main"}}
RUN = {
    "id": 10,
    "run_attempt": 1,
    "status": "completed",
    "path": ".github/workflows/quality-graph.yml",
    "event": "pull_request",
    "repository": {"full_name": "owner/repository"},
    "head_sha": HEAD,
    "html_url": "https://github.com/owner/repository/actions/runs/10",
    "pull_requests": [PULL],
}
RUNS = f"/actions/workflows/quality-graph.yml/runs?event=pull_request&head_sha={HEAD}&per_page=100"
CHECKS = f"/commits/{HEAD}/check-runs?check_name=Quality%20Graph&filter=all&per_page=100"
OPEN = "/pulls?state=open&base=main&per_page=100&page=1"
COMPLETED = {
    "id": 123,
    "external_id": "quality-graph:10:1",
    "app": {"slug": "github-actions"},
    "status": "completed",
}


def setup(*, source: str = GRAPH) -> MemoryGitHubPort:
    port = MemoryGitHubPort()
    configure_publication(port, source=source)
    port.responses.pop(("GET", "/pulls/42"))
    port.enqueue("GET", OPEN, [{"number": 42}])
    port.enqueue("GET", "/pulls/42", PULL)
    port.enqueue("GET", RUNS, {"total_count": 2, "workflow_runs": [RUN, RUN | {"id": 9}]})
    port.enqueue("GET", "/actions/runs/10", RUN)
    port.enqueue("GET", JOBS_PATH, {"jobs": []})
    return port


def set_response(port: MemoryGitHubPort, path: str, *values: JsonValue) -> None:
    port.responses["GET", path] = list(values)


def test_successful_reconcile_then_duplicate_tick_is_read_only() -> None:
    port = setup()
    artifacts = []
    for identifier, node in enumerate(("format", "lint"), start=1):
        archive = result_archive(node, node.title())
        artifacts.append(artifact(identifier, node, archive))
        port.downloads[f"/actions/artifacts/{identifier}/zip"] = archive
    port.enqueue("GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": artifacts})
    reconcile_open_pulls(port, "main")
    check = port.requests[-1]
    assert check[:2] == ("POST", "/check-runs")
    assert check[2]["conclusion"] == "success"
    assert check[2]["head_sha"] == HEAD
    assert check[2]["external_id"] == COMPLETED["external_id"]
    assert any(
        method == "POST" and path == "/issues/42/comments" for method, path, _ in port.requests
    )
    set_response(port, CHECKS, {"check_runs": [COMPLETED | {"app": {"slug": "other"}}] * 100})
    port.enqueue("GET", CHECKS + "&page=2", {"check_runs": [COMPLETED]})
    port.requests.clear()
    port.downloaded.clear()
    reconcile_open_pulls(port, "main")
    assert all(method == "GET" for method, _, _ in port.requests)
    assert port.downloaded == []


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("/pulls/42", None),
        ("/pulls/42", PULL | {"state": "closed"}),
        ("/pulls/42", PULL | {"number": 12}),
        ("/pulls/42", PULL | {"base": {"sha": BASE, "ref": "feature"}}),
        ("/pulls/42", PULL | {"head": {"sha": "non-sha"}}),
        ("/pulls/42", PULL | {"base": {"ref": "main", "sha": "non-sha"}}),
        (RUNS, {"total_count": 0, "workflow_runs": []}),
        (RUNS, {"total_count": 2, "workflow_runs": [RUN]}),
        ("/actions/runs/10", None),
        ("/actions/runs/10", RUN | {"id": 9}),
        ("/actions/runs/10", RUN | {"run_attempt": 2}),
        ("/actions/runs/10", RUN | {"repository": {"full_name": "other/repo"}}),
        ("/actions/runs/10", RUN | {"event": "push"}),
        ("/actions/runs/10", RUN | {"path": ".github/workflows/other.yml"}),
        ("/actions/runs/10", RUN | {"head_sha": BASE}),
        ("/actions/runs/10", RUN | {"pull_requests": []}),
        ("/actions/runs/10", RUN | {"pull_requests": [PULL, PULL]}),
        ("/actions/runs/10", RUN | {"pull_requests": [PULL | {"number": 41}]}),
        ("/actions/runs/10", RUN | {"pull_requests": [PULL | {"head": {"sha": BASE}}]}),
        ("/actions/runs/10", RUN | {"pull_requests": [PULL | {"base": {"sha": HEAD}}]}),
        (
            "/actions/runs/10",
            RUN | {"pull_requests": [PULL | {"base": {"sha": BASE, "ref": "old"}}]},
        ),
    ],
)
def test_ambiguous_wrong_or_stale_identity_never_writes(path: str, value: JsonValue) -> None:
    port = setup()
    set_response(port, path, value)
    reconcile_open_pulls(port, "main")
    assert all(method == "GET" and "/contents/" not in path for method, path, _ in port.requests)
    assert port.downloaded == []


@pytest.mark.parametrize("next_pull", [PULL, PULL | {"state": "closed"}])
def test_sample_live_run_and_recheck_before_writes(next_pull: dict[str, JsonValue]) -> None:
    port = setup()
    set_response(port, "/pulls/42", PULL, next_pull)
    set_response(port, "/actions/runs/10", RUN | {"status": "queued"})
    set_response(port, CHECKS, {"check_runs": [COMPLETED | {"status": "in_progress"}]})
    reconcile_open_pulls(port, "main")
    writes = [(path, value) for method, path, value in port.requests if method != "GET"]
    if next_pull == PULL:
        [(path, value)] = writes
        assert path == "/issues/42/comments"
        assert "waiting" in value["body"]
    else:
        assert writes == []
    assert port.downloaded == []


def test_reconcile_no_presentation_no_write() -> None:
    port = setup(source=SOURCE.replace("presentation: github-pr", "presentation: none"))
    reconcile_open_pulls(port, "main")
    assert all(method == "GET" for method, _, _ in port.requests)


def test_reconcile_attempt_uses_verified_job_history() -> None:
    port = setup()
    retry = RUN | {"run_attempt": 2}
    set_response(port, RUNS, {"total_count": 1, "workflow_runs": [retry]})
    set_response(port, "/actions/runs/10", retry)
    for attempt in (2, 1):
        port.enqueue(
            "GET", f"/actions/runs/10/attempts/{attempt}/jobs?per_page=100&page=1", {"jobs": []}
        )
    reconcile_open_pulls(port, "main")
    assert port.requests[-1][2]["conclusion"] == "failure"
    assert port.requests[-1][2]["external_id"] == "quality-graph:10:2"


@pytest.mark.parametrize("operation", ["reconcile", "scheduled-command"])
def test_runtime_requires_expected_default_branch_event(
    operation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    port = setup()
    event = {"repository": {"full_name": port.repository, "default_branch": "main"}}
    if operation == "scheduled-command":
        event.update(
            {"issue": {"number": 42, "pull_request": {}}, "comment": {"id": 1, "body": "/qg"}}
        )
        set_response(port, "/pulls/42", PULL | {"state": "closed"})
    else:
        set_response(port, OPEN, [])
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    monkeypatch.setattr("qg_github.runtime.HttpGitHubPort.from_environment", lambda: port)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_REPOSITORY", port.repository)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    expected_event = "schedule" if operation == "reconcile" else "issue_comment"
    monkeypatch.setenv("GITHUB_EVENT_NAME", expected_event)
    assert main([operation]) == 0
    for name, value in (
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_REPOSITORY", "other/repo"),
        ("GITHUB_EVENT_NAME", "pull_request"),
    ):
        with monkeypatch.context() as scoped:
            scoped.setenv(name, value)
            with pytest.raises(ValueError, match="trusted default-branch"):
                main([operation])
