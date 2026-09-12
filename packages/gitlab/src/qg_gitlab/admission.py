"""Admit results only from authoritative current GitLab job executions."""

from __future__ import annotations

import io
import stat
import zipfile
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from qg_gitlab.api import GitLabError, HttpGitLab, integer, object_value
from qg_gitlab.compiler import admission_job_name, graph_digest, job_name
from quality_graph_core.result import FailureKind, GitLabProvenance, Result, ResultStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

    from quality_graph_core.graph import Graph, Node
    from quality_graph_core.result import JsonValue

MAX_ARCHIVE_FILES = 100
BOOTSTRAP_KEY = "__admission__"
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
ACTIVE_STATES = {"created", "pending", "preparing", "running", "scheduled", "waiting_for_resource"}


@dataclass(frozen=True)
class Expectation:
    """Carry API-verified MR and pipeline identities plus trusted configuration."""

    project_id: int
    target_project_id: int
    merge_request: int
    pipeline_id: int
    head_sha: str
    flow_id: str
    declaration: Graph
    graph: Graph


@dataclass(frozen=True)
class NodeEvidence:
    """Keep native execution state separate from admitted portable results."""

    node_id: str
    status: ResultStatus
    job_id: int | None = None
    job_url: str = ""
    result: Result | None = None
    reason: str = ""
    native_status: str = ""


def archive_result(payload: bytes, path: str) -> Result:
    """Validate the entire archive before parsing its exact declared result."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_FILES:
            message = "GitLab artifact has too many archive entries"
            raise ValueError(message)
        total = 0
        candidates: list[bytes] = []
        for entry in entries:
            name = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in entry.filename
                or stat.S_ISLNK(mode)
            ):
                message = "GitLab artifact contains an unsafe archive entry"
                raise ValueError(message)
            total += entry.file_size
            if total > MAX_EXPANDED_BYTES:
                message = "GitLab artifact exceeds the expanded size limit"
                raise ValueError(message)
            if entry.filename == path:
                candidates.append(archive.read(entry))
        if not candidates:
            message = "GitLab job artifact does not contain the declared result"
            raise ValueError(message)
        if any(value != candidates[0] for value in candidates[1:]):
            message = "GitLab artifact contains conflicting duplicate results"
            raise ValueError(message)
        return Result.from_json(candidates[0])


def admit(api: HttpGitLab, expectation: Expectation) -> dict[str, NodeEvidence]:
    """Resolve the current execution of each job before downloading any result."""
    endpoint = f"/projects/{expectation.project_id}/pipelines/{expectation.pipeline_id}/jobs"
    jobs = tuple(api.pages(f"{endpoint}?include_retried=false"))
    output: dict[str, NodeEvidence] = {BOOTSTRAP_KEY: _bootstrap(expectation, jobs)}
    for node in expectation.graph.nodes:
        matches = tuple(
            job for job in jobs if job.get("name") == job_name(expectation.flow_id, node.id)
        )
        if len(matches) != 1:
            output[node.id] = NodeEvidence(
                node.id, ResultStatus.FAILED, reason="Missing or ambiguous current job evidence"
            )
            continue
        try:
            output[node.id] = _admit_job(api, expectation, node, matches[0])
        except (GitLabError, OSError, TypeError, ValueError, zipfile.BadZipFile) as error:
            identity = matches[0].get("id")
            url = matches[0].get("web_url")
            output[node.id] = NodeEvidence(
                node.id,
                ResultStatus.FAILED,
                identity
                if isinstance(identity, int) and not isinstance(identity, bool) and identity > 0
                else None,
                url if isinstance(url, str) else "",
                reason=str(error),
            )
        status = matches[0].get("status")
        output[node.id] = replace(
            output[node.id], native_status=status if isinstance(status, str) else "unknown"
        )
    return output


def _bootstrap(expected: Expectation, jobs: tuple[dict[str, JsonValue], ...]) -> NodeEvidence:
    matches = [job for job in jobs if job.get("name") == admission_job_name(expected.flow_id)]
    if len(matches) != 1:
        return NodeEvidence(
            BOOTSTRAP_KEY,
            ResultStatus.FAILED,
            reason="Missing or ambiguous publisher admission job",
        )
    job = matches[0]
    identity: int | None = None
    try:
        identity = integer(job.get("id"), "admission job ID")
        pipeline = object_value(job.get("pipeline"), "admission pipeline")
        valid = (
            integer(pipeline.get("id"), "admission pipeline ID") == expected.pipeline_id
            and integer(pipeline.get("project_id"), "admission project") == expected.project_id
            and pipeline.get("sha") == expected.head_sha
        )
    except (TypeError, ValueError):
        valid = False
        identity = None
    status = job.get("status")
    state = ResultStatus.FAILED
    if valid and isinstance(status, str):
        if status in ACTIVE_STATES:
            state = ResultStatus.IN_PROGRESS
        elif status == "success":
            state = ResultStatus.PASSED
        elif status in {"canceled", "canceling"}:
            state = ResultStatus.CANCELLED
    url = job.get("web_url")
    return NodeEvidence(
        BOOTSTRAP_KEY,
        state,
        identity,
        url if isinstance(url, str) else "",
        reason=""
        if state is ResultStatus.PASSED
        else "Publisher admission has not completed successfully",
        native_status=status if isinstance(status, str) else "unknown",
    )


def _admit_job(
    api: HttpGitLab, expected: Expectation, node: Node, job: Mapping[str, JsonValue]
) -> NodeEvidence:
    identity = integer(job.get("id"), "job ID")
    pipeline = object_value(job.get("pipeline"), "job pipeline")
    if (
        integer(pipeline.get("id"), "job pipeline ID") != expected.pipeline_id
        or integer(pipeline.get("project_id"), "job pipeline project") != expected.project_id
        or pipeline.get("sha") != expected.head_sha
    ):
        message = "GitLab job belongs to another pipeline, project or commit"
        raise ValueError(message)
    url = job.get("web_url")
    job_url = url if isinstance(url, str) else ""
    status = job.get("status")
    if isinstance(status, str) and status in ACTIVE_STATES:
        return NodeEvidence(node.id, ResultStatus.IN_PROGRESS, identity, job_url)
    if status in {"canceled", "canceling"}:
        return NodeEvidence(
            node.id,
            ResultStatus.CANCELLED,
            identity,
            job_url,
            reason="GitLab canceled this execution",
        )
    if status == "skipped":
        return NodeEvidence(
            node.id, ResultStatus.SKIPPED, identity, job_url, reason="GitLab skipped this execution"
        )
    if status not in {"success", "failed"}:
        message = "GitLab job is not a recognized terminal execution"
        raise ValueError(message)
    path = f".qg/results/{expected.flow_id}/{node.id}.json"
    result = archive_result(
        api.download(f"/projects/{expected.project_id}/jobs/{identity}/artifacts"), path
    )
    provenance = GitLabProvenance(
        api.server_url,
        expected.project_id,
        expected.head_sha,
        expected.pipeline_id,
        identity,
        graph_digest(expected.declaration),
        expected.target_project_id,
        expected.merge_request,
        expected.flow_id,
        node.operation_id or node.id,
    )
    if result.provenance != provenance or result.node_id != node.id or result.title != node.title:
        message = "GitLab result provenance does not match the current declared job"
        raise ValueError(message)
    if status == "failed" and (
        result.failure_kind is not FailureKind.QUALITY or job.get("allow_failure") is not True
    ):
        if result.failure_kind in {None, FailureKind.QUALITY}:
            message = "Passed GitLab result contradicts failed native execution"
            raise ValueError(message)
        return NodeEvidence(
            node.id, ResultStatus.FAILED, identity, job_url, result, "Native execution failed"
        )
    if status == "success" and result.status not in {ResultStatus.PASSED, ResultStatus.SKIPPED}:
        message = "GitLab result contradicts successful native execution"
        raise ValueError(message)
    return NodeEvidence(node.id, result.status, identity, job_url, result)
