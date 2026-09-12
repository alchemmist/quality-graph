"""Accept declaration migrations only when the trusted PR contract is unchanged."""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING
from urllib.parse import quote

from qg_github.artifacts import ArtifactError, DeclarationMismatchError, download_results
from qg_github.compiler import compile_graph, pr_contract
from qg_github.github import GITHUB_PAGE_SIZE, GitHubError
from qg_github.presentation import pr_presentation_graph
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from qg_github.artifacts import ArtifactExpectation
    from qg_github.github import GitHubPort
    from quality_graph_core.result import JsonValue, Result

MAX_DECLARATION_BYTES = 1_048_576


def read_pr_results(
    port: GitHubPort, graph: Graph, expectation: ArtifactExpectation
) -> tuple[Graph, dict[str, Result]]:
    """Verify run provenance and allow only semantically identical PR declaration migrations."""
    expectation = _execution_expectation(port, graph, expectation)
    try:
        return graph, download_results(port, expectation)
    except DeclarationMismatchError as mismatch:
        try:
            head = _head_declaration(port, expectation.head_sha)
            compiled = compile_graph(head)
            projected = pr_presentation_graph(head)
        except (GitHubError, ValueError, TypeError) as error:
            raise mismatch from error
        if (
            projected is None
            or compiled.graph_digest == expectation.graph_digest
            or pr_contract(projected) != pr_contract(graph)
        ):
            raise
        current = replace(
            expectation,
            graph_digest=compiled.graph_digest,
            flow_id=projected.flow_id,
            operation_ids={
                node.id: node.operation_id
                for node in projected.nodes
                if node.operation_id is not None
            },
        )
        return projected, download_results(port, current)


def _head_declaration(port: GitHubPort, sha: str) -> Graph:
    value = port.request("GET", f"/contents/qg.yaml?ref={quote(sha, safe='')}")
    if not isinstance(value, dict):
        message = "head declaration response must be an object"
        raise TypeError(message)
    content = value.get("content")
    if not isinstance(content, str):
        message = "head declaration must be text"
        raise TypeError(message)
    decoded = base64.b64decode("".join(content.split()), validate=True)
    if len(decoded) > MAX_DECLARATION_BYTES:
        message = "head declaration exceeds the size limit"
        raise ValueError(message)
    return Graph.from_yaml(decoded.decode())


def _execution_expectation(
    port: GitHubPort, graph: Graph, expectation: ArtifactExpectation
) -> ArtifactExpectation:
    if expectation.run_attempt < 1:
        message = "workflow attempt must be positive"
        raise ArtifactError(message)
    if expectation.run_attempt == 1:
        return expectation
    by_title = {node.title: node.id for node in graph.nodes}
    attempts: dict[str, int] = {}
    conclusions: dict[str, str] = {}
    for attempt in range(expectation.run_attempt, 0, -1):
        jobs = _attempt_jobs(port, expectation.workflow_run_id, attempt)
        matched: set[str] = set()
        for job in jobs:
            name = job.get("name")
            node_id = by_title.get(name) if isinstance(name, str) else None
            if node_id is None or node_id in attempts:
                continue
            if node_id in matched:
                message = f"ambiguous workflow jobs for node {node_id} attempt {attempt}"
                raise ArtifactError(message)
            matched.add(node_id)
            conclusion = job.get("conclusion")
            if (
                job.get("run_id") != expectation.workflow_run_id
                or job.get("run_attempt") != attempt
                or job.get("status") != "completed"
                or not isinstance(conclusion, str)
            ):
                message = f"invalid workflow job evidence for node {node_id} attempt {attempt}"
                raise ArtifactError(message)
            conclusions[node_id] = conclusion
        attempts.update(dict.fromkeys(matched, attempt))
        if expectation.node_ids <= attempts.keys():
            return replace(expectation, node_attempts=attempts, node_conclusions=conclusions)
    message = "missing workflow job evidence for result artifacts"
    raise ArtifactError(message)


def _attempt_jobs(port: GitHubPort, run_id: int, attempt: int) -> list[dict[str, JsonValue]]:
    jobs: list[dict[str, JsonValue]] = []
    page = 1
    while True:
        response = port.request(
            "GET",
            f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page={GITHUB_PAGE_SIZE}&page={page}",
        )
        values = response.get("jobs") if isinstance(response, dict) else None
        if not isinstance(values, list) or any(not isinstance(job, dict) for job in values):
            message = f"invalid workflow jobs response for attempt {attempt}"
            raise ArtifactError(message)
        jobs.extend(job for job in values if isinstance(job, dict))
        if len(values) < GITHUB_PAGE_SIZE:
            return jobs
        page += 1
