from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest

from qg_github.commands import CommandOutcome, dispatch_pr_command
from qg_github.github import GitHubError, MemoryGitHubPort
from qg_github.reconciliation import reconcile_open_pulls
from qg_github.runtime import main
from tests.test_commands import direct_event
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
        ("/actions/runs/10", RUN | {"status": "unknown"}),
        (RUNS, {"total_count": 1001, "workflow_runs": []}),
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


@pytest.mark.parametrize(
    ("operation", "expected_event"),
    [
        ("reconcile", "schedule"),
        ("reconcile", "workflow_dispatch"),
        ("scheduled-command", "issue_comment"),
    ],
)
def test_runtime_requires_expected_default_branch_event(
    operation: str, expected_event: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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


@pytest.mark.parametrize(
    ("branch", "state", "presentation", "enabled"),
    [
        ("main", "open", "github-pr", True),
        ("main", "closed", "github-pr", False),
        ("feature", "open", "github-pr", False),
        ("main", "open", "none", False),
    ],
)
def test_scheduled_command_uses_admitted_open_pr_snapshot(
    branch: str, state: str, presentation: str, *, enabled: bool
) -> None:
    port = setup(source=SOURCE.replace("presentation: github-pr", f"presentation: {presentation}"))
    set_response(port, "/pulls/42", PULL | {"state": state, "base": {"ref": branch, "sha": BASE}})
    port.enqueue("POST", "/issues/comments/10/reactions", None)
    assert dispatch_pr_command(
        port, direct_event("/qg status"), default_branch="main"
    ) == CommandOutcome(handled=enabled, authorized=enabled)
    assert port.requests.count(("GET", "/pulls/42", None)) == 1
    if branch == "main" and state == "open":
        assert ("GET", f"/contents/qg.yaml?ref={BASE}", None) in port.requests
    else:
        assert len(port.requests) == 1
    if enabled:
        post, reaction = [call for call in port.requests if call[0] != "GET"]
        assert post[0:2] == ("POST", "/issues/42/comments")
        assert (
            post[2]["body"]
            == f"Quality Graph status for `{HEAD}`: no current Quality Graph execution found."
        )
        assert reaction == ("POST", "/issues/comments/10/reactions", {"content": "+1"})
    else:
        assert all(method == "GET" for method, _, _ in port.requests)


@pytest.mark.parametrize("body", ["hello", "/qg!"])
def test_scheduled_ignores_non_commands_before_reading_pr(body: str) -> None:
    port = MemoryGitHubPort()
    assert dispatch_pr_command(port, direct_event(body), default_branch="main") == CommandOutcome(
        handled=False
    )
    assert port.requests == []


@pytest.mark.parametrize(
    ("pull", "error"),
    [
        (None, "pull request must be an object"),
        ({"base": []}, "pull request base must be an object"),
        (PULL | {"base": {"ref": "main"}}, "pull request base SHA must be a string"),
    ],
)
def test_scheduled_command_rejects_malformed_pr_snapshot(pull: JsonValue, error: str) -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", "/pulls/42", pull)
    with pytest.raises(TypeError, match=f"^{error}$"):
        dispatch_pr_command(port, direct_event("/qg help"), default_branch="main")
    assert port.requests == [("GET", "/pulls/42", None)]


@pytest.mark.parametrize("second_id", [10, 102])
def test_full_run_listing_finds_latest_attempt_on_second_page_without_accepting_overlap(
    second_id: int,
) -> None:
    port = setup()
    counts = {"total_count": 101}
    set_response(port, RUNS, counts | {"workflow_runs": [RUN | {"id": i} for i in range(1, 101)]})
    set_response(port, RUNS + "&page=2", counts | {"workflow_runs": [RUN | {"id": second_id}]})
    port.enqueue("GET", "/actions/runs/102", RUN | {"id": 102})
    set_response(port, CHECKS, {"check_runs": [COMPLETED | {"external_id": "quality-graph:102:1"}]})
    reconcile_open_pulls(port, "main")
    assert (("GET", "/actions/runs/102", None) in port.requests) == (second_id == 102)
    assert all(method == "GET" for method, _, _ in port.requests)


def test_repeated_check_page_never_posts_a_duplicate() -> None:
    port = setup()
    full = {"check_runs": [COMPLETED | {"id": i, "external_id": "other"} for i in range(100)]}
    set_response(port, CHECKS, full)
    port.enqueue("GET", CHECKS + "&page=2", full)
    with pytest.raises(RuntimeError, match="#42: ValueError"):
        reconcile_open_pulls(port, "main")
    assert all(method == "GET" for method, _, _ in port.requests)


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("denied", "Reconciliation failed for #7: POST HTTP 403 integration permission denied"),
        ("throttled", "GitHub POST /issues/7/comments failed: HTTP 403 (rate limited)"),
        ("yaml", "Reconciliation failed for #7: ParserError"),
    ],
)
def test_one_pr_failure_never_completes_its_check_or_masks_tick_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str, expected_error: str
) -> None:
    port = setup()
    set_response(port, OPEN, [{"number": 7}, {"number": 42}])
    first_sha = "c" * 40
    first_pull = PULL | {"number": 7, "head": {"sha": first_sha}}
    if failure == "yaml":
        first_base = "e" * 40
        first_pull["base"] = {"ref": "main", "sha": first_base}
        port.enqueue(
            "GET",
            f"/contents/qg.yaml?ref={first_base}",
            {"content": base64.b64encode(b"hidden: [").decode()},
        )
    first_run = RUN | {"id": 9, "head_sha": first_sha, "pull_requests": [first_pull]}
    port.enqueue("GET", "/pulls/7", first_pull)
    port.enqueue(
        "GET", RUNS.replace(HEAD, first_sha), {"total_count": 1, "workflow_runs": [first_run]}
    )
    port.enqueue("GET", "/actions/runs/9", first_run)
    port.enqueue("GET", CHECKS.replace(HEAD, first_sha), {"check_runs": []})
    port.enqueue("GET", "/actions/runs/9/artifacts?per_page=100&page=1", {"artifacts": []})
    port.enqueue("GET", JOBS_PATH.replace("/10/", "/9/"), {"jobs": []})
    port.enqueue("GET", "/issues/7/comments?per_page=100&page=1", [])
    descriptors = []
    for identifier, node in enumerate(("format", "lint"), start=1):
        archive = result_archive(node, node.title())
        descriptors.append(artifact(identifier, node, archive))
        port.downloads[f"/actions/artifacts/{identifier}/zip"] = archive
    port.enqueue(
        "GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": descriptors}
    )
    original = port.request

    def request(method: str, path: str, payload: JsonValue = None) -> JsonValue:
        if (method, path) == ("POST", "/issues/7/comments"):
            reason = "rate limited" if failure == "throttled" else "integration permission denied"
            raise GitHubError(method, path, 403, reason)
        return original(method, path, payload)

    monkeypatch.setattr(port, "request", request)
    with pytest.raises(RuntimeError) as raised:
        reconcile_open_pulls(port, "main")
    assert str(raised.value) == expected_error
    writes = [payload for _, path, payload in port.requests if path == "/check-runs"]
    assert [(check["head_sha"], check["conclusion"]) for check in writes] == (
        [] if failure == "throttled" else [(HEAD, "success")]
    )


def test_rate_limit_during_optional_job_lookup_stops_before_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = setup()
    set_response(port, OPEN, [{"number": 42}, {"number": 7}])
    port.enqueue("GET", "/actions/runs/10/artifacts?per_page=100&page=1", {"artifacts": []})
    original = port.request

    def request(method: str, path: str, payload: JsonValue = None) -> JsonValue:
        if path == JOBS_PATH:
            raise GitHubError(method, path, 403, "secondary rate limit")
        return original(method, path, payload)

    monkeypatch.setattr(port, "request", request)
    with pytest.raises(GitHubError, match="secondary rate limit"):
        reconcile_open_pulls(port, "main")
    assert all(method == "GET" and path != "/pulls/7" for method, path, _ in port.requests)


def test_throttle_reading_pr_graph_is_not_a_declaration_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = setup()
    archive = result_archive("format", "Formatting", graph_digest="c" * 64)
    port.enqueue(
        "GET",
        "/actions/runs/10/artifacts?per_page=100&page=1",
        {"artifacts": [artifact(1, "format", archive)]},
    )
    port.downloads["/actions/artifacts/1/zip"] = archive
    original = port.request

    def request(method: str, path: str, payload: JsonValue = None) -> JsonValue:
        if path == f"/contents/qg.yaml?ref={HEAD}":
            raise GitHubError(method, path, 429, "rate limited")
        return original(method, path, payload)

    monkeypatch.setattr(port, "request", request)
    with pytest.raises(GitHubError, match="rate limited"):
        reconcile_open_pulls(port, "main")
    assert all(method == "GET" for method, _, _ in port.requests)
