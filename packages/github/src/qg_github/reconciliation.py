"""Discover publishable attempts using only repository-scoped GitHub metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

import yaml

from qg_github.github import GITHUB_PAGE_SIZE, GitHubError, GitHubPort, paged
from qg_github.publication import PullRequestState, WorkflowRunEvent, reconcile_publication
from quality_graph_core.json_values import JSON

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quality_graph_core.result import JsonValue

SHA = re.compile(r"[0-9a-f]{40}")
WORKFLOW_PATH = ".github/workflows/quality-graph.yml"
RUN_STATUSES = ("queued", "in_progress", "completed", "waiting", "requested", "pending")
SEARCH_RUN_LIMIT = 1_000


def default_branch_for_event(
    environment: Mapping[str, str], event: Mapping[str, JsonValue], *expected_events: str
) -> str:
    """Require the expected default-branch event in the runner's own repository."""
    repo = JSON.object(event.get("repository"), "event repository")
    branch = JSON.string(repo.get("default_branch"), "default branch")
    if (
        environment.get("GITHUB_EVENT_NAME") not in expected_events
        or repo.get("full_name") != environment["GITHUB_REPOSITORY"]
        or environment.get("GITHUB_REF") != f"refs/heads/{branch}"
    ):
        message = "expected a trusted default-branch event"
        raise ValueError(message)
    return branch


@dataclass(frozen=True)
class _Candidate:
    pull: PullRequestState
    event: WorkflowRunEvent


def reconcile_open_pulls(port: GitHubPort, branch: str) -> None:
    """Sample each open default-branch PR once and revalidate immediately before writing."""
    failures: list[str] = []
    for item in paged(port, f"/pulls?state=open&base={quote(branch, safe='')}"):
        number = JSON.integer(item.get("number"), "pull request number")
        try:
            _reconcile_pull(port, branch, number)
        except GitHubError as error:
            if error.throttled:
                raise
            failures.append(
                f"#{number}: {error.method} HTTP {error.status_code} {error.reason}".rstrip()
            )
        except (TypeError, ValueError, RuntimeError, yaml.YAMLError) as error:
            failures.append(f"#{number}: {type(error).__name__}")
    if failures:
        message = "Reconciliation failed for " + ", ".join(failures)
        raise RuntimeError(message)


def _reconcile_pull(port: GitHubPort, branch: str, number: int) -> None:
    candidate = _candidate(port, branch, number)
    if candidate is not None:
        reconcile_publication(
            port,
            candidate.event,
            candidate.pull,
            is_current=lambda: candidate == _candidate(port, branch, number),
        )


def _candidate(port: GitHubPort, branch: str, number: int) -> _Candidate | None:
    pull = port.request("GET", f"/pulls/{number}")
    if not isinstance(pull, dict) or pull.get("state") != "open" or pull.get("number") != number:
        return None
    base = JSON.object(pull.get("base"), "PR base")
    head = JSON.object(pull.get("head"), "PR head")
    head_sha = JSON.string(head.get("sha"), "PR head SHA")
    base_sha = JSON.string(base.get("sha"), "PR base SHA")
    if base.get("ref") != branch or not SHA.fullmatch(head_sha) or not SHA.fullmatch(base_sha):
        return None
    state = PullRequestState(number, head_sha, base_sha)
    latest = _latest_head_run(port, head_sha)
    if latest is None:
        return None
    run = port.request("GET", f"/actions/runs/{latest[0]}")
    if not isinstance(run, dict) or (run.get("id"), run.get("run_attempt")) != latest:
        return None
    if not _matches_run(run, port.repository, state, branch):
        return None
    event = WorkflowRunEvent.from_value({"action": run["status"], "workflow_run": run})
    return _Candidate(state, event)


def _latest_head_run(port: GitHubPort, sha: str) -> tuple[int, int] | None:
    path = f"/actions/workflows/quality-graph.yml/runs?event=pull_request&head_sha={sha}"
    path = f"{path}&per_page={GITHUB_PAGE_SIZE}"
    first = JSON.object(port.request("GET", path), "workflow runs")
    count = JSON.integer(first.get("total_count"), "workflow run count")
    if not 0 < count <= SEARCH_RUN_LIMIT:
        return None
    runs: dict[int, int] = {}
    for offset in range(0, count, GITHUB_PAGE_SIZE):
        response = (
            first
            if offset == 0
            else JSON.object(
                port.request("GET", f"{path}&page={offset // GITHUB_PAGE_SIZE + 1}"),
                "workflow runs",
            )
        )
        values = JSON.array(response.get("workflow_runs"), "workflow runs")
        if response.get("total_count") != count or len(values) != min(
            GITHUB_PAGE_SIZE, count - offset
        ):
            return None
        for value in values:
            run = JSON.object(value, "workflow run")
            runs[JSON.integer(run.get("id"), "run id")] = JSON.integer(
                run.get("run_attempt"), "attempt"
            )
    return max(runs.items()) if len(runs) == count else None


def _matches_run(
    run: dict[str, JsonValue], repository: str, state: PullRequestState, branch: str
) -> bool:
    associations = JSON.array(run.get("pull_requests"), "associated pull requests")
    if len(associations) != 1:
        return False
    pull = JSON.object(associations[0], "associated PR")
    base = JSON.object(pull.get("base"), "associated PR base")
    head = JSON.object(pull.get("head"), "associated PR head")
    repo = JSON.object(run.get("repository"), "workflow repository")
    return (
        repo.get("full_name") == repository
        and run.get("status") in RUN_STATUSES
        and run.get("path") == WORKFLOW_PATH
        and run.get("event") == "pull_request"
        and run.get("head_sha") == state.head_sha
        and pull.get("number") == state.number
        and head.get("sha") == state.head_sha
        and base.get("sha") == state.base_sha
        and base.get("ref") == branch
        and JSON.integer(run.get("id"), "run id") > 0
        and JSON.integer(run.get("run_attempt"), "attempt") > 0
    )
