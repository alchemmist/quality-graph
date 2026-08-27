"""Publish trusted live and final pull-request Quality Graph state."""

from __future__ import annotations

import base64
import json
import urllib.parse
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from qg_github.approvals import approval_ledger
from qg_github.artifacts import ArtifactError, ArtifactExpectation, download_results
from qg_github.comments import find_managed_comment, upsert_managed_comment
from qg_github.compiler import compile_graph
from qg_github.dashboard import (
    DASHBOARD_MARKER,
    DashboardModel,
    DashboardRun,
    final_dashboard,
    pending_dashboard,
    render_dashboard,
)
from qg_github.github import GITHUB_PAGE_SIZE, GitHubPort
from qg_github.labels import parse_label_state, reconcile_labels
from quality_graph_core.graph import Graph
from quality_graph_core.policy import effective_graph
from quality_graph_core.result import JsonValue, ResultStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quality_graph_core.result import Result


@dataclass(frozen=True)
class WorkflowRunEvent:
    """Carry trusted workflow-run identity from one GitHub event."""

    action: str
    event: str
    id: int
    attempt: int
    workflow_head_sha: str
    pull_head_sha: str | None
    url: str
    pull_request: int | None

    @classmethod
    def from_value(cls, value: JsonValue) -> WorkflowRunEvent:
        """Narrow an untrusted GitHub event payload."""
        event = _object(value, "workflow event")
        run = _object(event.get("workflow_run"), "workflow run")
        pulls = _array(run.get("pull_requests", []), "workflow pull requests")
        pull_number: int | None = None
        pull_head: str | None = None
        if pulls:
            pull = _object(pulls[0], "workflow pull request")
            pull_number = _optional_integer(pull.get("number"), "pull request number")
            head = pull.get("head")
            if isinstance(head, dict):
                pull_head = _optional_string(head.get("sha"), "pull request head SHA")
        return cls(
            _string(event.get("action"), "workflow action"),
            _string(run.get("event"), "workflow trigger event"),
            _integer(run.get("id"), "workflow run id"),
            _integer(run.get("run_attempt", 1), "workflow run attempt"),
            _string(run.get("head_sha"), "workflow head SHA"),
            pull_head,
            _string(run.get("html_url"), "workflow run URL"),
            pull_number,
        )


@dataclass(frozen=True)
class PullRequestState:
    """Carry current trusted pull-request and base revision state."""

    number: int
    head_sha: str
    base_sha: str


@dataclass(frozen=True)
class PublicationOutcome:
    """Describe whether and what the publisher updated."""

    published: bool
    status: ResultStatus | None = None
    comment_id: int | None = None


def publish_workflow_run(
    port: GitHubPort,
    event_value: JsonValue,
) -> PublicationOutcome:
    """Publish one trusted workflow-run event if it is current."""
    event = WorkflowRunEvent.from_value(event_value)
    if event.event != "pull_request":
        return PublicationOutcome(published=False)
    number = event.pull_request or _resolve_pull_request(port, event.workflow_head_sha)
    if number is None:
        return PublicationOutcome(published=False)
    pull = _pull_request(port, number)
    if event.pull_head_sha is not None and event.pull_head_sha != pull.head_sha:
        return PublicationOutcome(published=False)
    if not _is_latest_run(port, event, number):
        return PublicationOutcome(published=False)
    graph = Graph.from_yaml(_repository_file(port, "quality-graph.yml", pull.base_sha))
    compiled = compile_graph(graph)
    run = DashboardRun(event.id, event.attempt, pull.head_sha, event.url)
    effective_results: Mapping[str, Result] | None = None
    if event.action in {"requested", "in_progress"}:
        model = pending_dashboard(graph, run, started=event.action == "in_progress")
    elif event.action == "completed":
        model, effective_results = _completed_dashboard(
            port,
            graph,
            compiled.graph_digest,
            pull,
            run,
        )
    else:
        message = f"unsupported workflow_run action: {event.action}"
        raise ValueError(message)
    existing = find_managed_comment(port, number, DASHBOARD_MARKER)
    previous_labels = parse_label_state(existing.body) if existing is not None else frozenset()
    if effective_results is None and previous_labels:
        model = replace(model, managed_labels=tuple(sorted(previous_labels)))
    comment = upsert_managed_comment(port, number, DASHBOARD_MARKER, render_dashboard(model))
    _publish_check(port, model)
    if effective_results is not None:
        reconcile_labels(
            port,
            number,
            graph,
            effective_results,
            previous_owned=previous_labels,
        )
    return PublicationOutcome(published=True, status=model.status, comment_id=comment.id)


def _completed_dashboard(
    port: GitHubPort,
    graph: Graph,
    graph_digest: str,
    pull: PullRequestState,
    run: DashboardRun,
) -> tuple[DashboardModel, Mapping[str, Result] | None]:
    expectation = ArtifactExpectation(
        port.repository,
        pull.number,
        pull.head_sha,
        run.id,
        graph_digest,
        frozenset(node.id for node in graph.nodes),
    )
    try:
        results = download_results(port, expectation)
    except ArtifactError as error:
        pending = pending_dashboard(graph, run)
        return (
            replace(
                pending,
                status=ResultStatus.FAILED,
                message=f"The final dashboard could not be assembled: {error}",
                rows=tuple(replace(row, status=ResultStatus.FAILED) for row in pending.rows),
            ),
            None,
        )
    approvals = approval_ledger(port, pull.number)
    effective = effective_graph(graph, results, approvals)
    model = final_dashboard(graph, effective.results, run)
    missing = expectation.node_ids - results.keys()
    if missing:
        model = replace(
            model,
            status=ResultStatus.FAILED,
            message=f"Missing result artifacts for nodes: {', '.join(sorted(missing))}.",
        )
        return model, None
    return model, effective.results


def _pull_request(port: GitHubPort, number: int) -> PullRequestState:
    pull = _object(port.request("GET", f"/pulls/{number}"), "pull request")
    head = _object(pull.get("head"), "pull request head")
    base = _object(pull.get("base"), "pull request base")
    return PullRequestState(
        number,
        _string(head.get("sha"), "pull request head SHA"),
        _string(base.get("sha"), "pull request base SHA"),
    )


def _resolve_pull_request(port: GitHubPort, head_sha: str) -> int | None:
    pulls = _array(port.request("GET", f"/commits/{head_sha}/pulls"), "commit pull requests")
    numbers: list[int] = []
    for value in pulls:
        pull = _object(value, "commit pull request")
        number = _optional_integer(pull.get("number"), "pull request number")
        if number is not None:
            numbers.append(number)
    return max(numbers) if numbers else None


def _is_latest_run(port: GitHubPort, event: WorkflowRunEvent, number: int) -> bool:
    path = (
        "/actions/workflows/quality-graph.yml/runs?event=pull_request"
        f"&per_page={GITHUB_PAGE_SIZE}&page=1"
    )
    response = _object(port.request("GET", path), "workflow runs")
    runs = _array(response.get("workflow_runs", []), "workflow runs")
    matching_ids = [
        run_id
        for value in runs
        for run in (_object(value, "workflow run"),)
        if _run_has_pull(run, number)
        for run_id in (_optional_integer(run.get("id"), "workflow run id"),)
        if run_id is not None
    ]
    return not matching_ids or event.id >= max(matching_ids)


def _run_has_pull(run: Mapping[str, JsonValue], number: int) -> bool:
    return any(
        _object(value, "workflow pull request").get("number") == number
        for value in _array(run.get("pull_requests", []), "workflow pull requests")
    )


def _repository_file(port: GitHubPort, path: str, ref: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="")
    encoded_ref = urllib.parse.quote(ref, safe="")
    response = _object(
        port.request("GET", f"/contents/{encoded_path}?ref={encoded_ref}"),
        "repository file",
    )
    content = _string(response.get("content"), "repository file content")
    try:
        return base64.b64decode(content.replace("\n", ""), validate=True).decode()
    except (ValueError, UnicodeDecodeError) as error:
        message = f"repository file is not valid base64 UTF-8: {path}"
        raise ValueError(message) from error


def _publish_check(port: GitHubPort, model: DashboardModel) -> None:
    completed = model.status not in {ResultStatus.WAITING, ResultStatus.IN_PROGRESS}
    payload: dict[str, JsonValue] = {
        "name": "Quality Graph",
        "head_sha": model.head_sha,
        "status": "completed" if completed else "in_progress",
        "details_url": next((row.logs_url for row in model.rows), ""),
        "output": {
            "title": "Quality Graph",
            "summary": model.message,
        },
    }
    if completed:
        payload["conclusion"] = "success" if model.status is ResultStatus.PASSED else "failure"
    port.request("POST", "/check-runs", payload)


def read_event_json(value: str) -> dict[str, JsonValue]:
    """Decode and narrow one GitHub event JSON document."""
    event = cast("JsonValue", json.loads(value))
    return _object(event, "GitHub event")


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise TypeError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    return value


def _optional_string(value: JsonValue, context: str) -> str | None:
    return None if value is None else _string(value, context)


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise TypeError(message)
    return value


def _optional_integer(value: JsonValue, context: str) -> int | None:
    return None if value is None else _integer(value, context)
