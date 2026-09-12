from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from typing import TYPE_CHECKING

import httpx
import pytest

from qg_gitlab import admission
from qg_gitlab.admission import Expectation, admit, archive_result
from qg_gitlab.api import HttpGitLab
from qg_gitlab.compiler import graph_digest, starter
from quality_graph_core.graph import Graph
from quality_graph_core.result import FailureKind, GitLabProvenance, Result, ResultStatus

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue

SERVER = "https://gitlab.example.test"
HEAD = "a" * 40
PATH = ".qg/results/mr/quality.json"


def expected() -> Expectation:
    graph = Graph.from_yaml(starter("main"))
    return Expectation(10, 20, 3, 40, HEAD, "mr", graph, graph)


def result() -> Result:
    graph = expected().graph
    return Result(
        "quality",
        "Quality",
        ResultStatus.PASSED,
        GitLabProvenance(SERVER, 10, HEAD, 40, 50, graph_digest(graph), 20, 3, "mr", "quality"),
    )


def archive(value: Result, path: str = PATH) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        output.writestr(path, value.to_json())
    return buffer.getvalue()


def job() -> dict[str, JsonValue]:
    return {
        "id": 50,
        "name": "qg:mr:quality",
        "status": "success",
        "allow_failure": False,
        "pipeline": {"id": 40, "project_id": 10, "sha": HEAD},
    }


def evaluate(jobs: list[JsonValue], payload: bytes | None) -> admission.NodeEvidence:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=jobs)
        if payload is None:
            return httpx.Response(404)
        return httpx.Response(200, content=payload)

    with HttpGitLab(SERVER, "fake-token", transport=httpx.MockTransport(respond)) as api:
        return admit(api, expected())["quality"]


def test_current_result_is_bound_to_one_native_job() -> None:
    admitted = evaluate([job()], archive(result()))
    assert admitted.status is ResultStatus.PASSED
    assert admitted.job_id == 50
    assert admitted.result == result()


@pytest.mark.parametrize(
    "status", ["created", "pending", "preparing", "running", "scheduled", "waiting_for_resource"]
)
def test_active_execution_does_not_admit_an_old_result(status: str) -> None:
    observed = job()
    observed["status"] = status
    admitted = evaluate([observed], archive(result()))
    assert admitted.status is ResultStatus.IN_PROGRESS
    assert admitted.result is None


@pytest.mark.parametrize("status", ["canceled", "canceling", "skipped", "manual", "unknown"])
def test_non_successful_native_state_does_not_admit_success(status: str) -> None:
    observed = job()
    observed["status"] = status
    admitted = evaluate([observed], archive(result()))
    assert admitted.status is not ResultStatus.PASSED
    assert admitted.result is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", 41), ("project_id", 11), ("sha", "b" * 40), ("id", True), ("project_id", True)],
)
def test_native_pipeline_metadata_must_match(field: str, value: JsonValue) -> None:
    observed = job()
    pipeline = observed["pipeline"]
    assert isinstance(pipeline, dict)
    pipeline[field] = value
    admitted = evaluate([observed], archive(result()))
    assert admitted.status is ResultStatus.FAILED
    assert admitted.result is None


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_native_jobs_fail_closed(count: int) -> None:
    assert evaluate([job() for _ in range(count)], archive(result())).status is ResultStatus.FAILED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jobId", 49),
        ("jobId", 51),
        ("pipelineId", 41),
        ("projectId", 11),
        ("targetProjectId", 21),
        ("mergeRequest", 4),
        ("headSha", "b" * 40),
        ("graphDigest", "f" * 64),
        ("flowId", "another"),
        ("operationId", "another"),
        ("serverUrl", "https://another.example.test"),
    ],
)
def test_foreign_result_provenance_never_establishes_success(field: str, value: JsonValue) -> None:
    source = result().to_value()
    provenance = source["provenance"]
    assert isinstance(provenance, dict)
    provenance[field] = value
    observed = evaluate([job()], archive(Result.from_value(source)))
    assert observed.status is ResultStatus.FAILED
    assert observed.result is None


@pytest.mark.parametrize(("field", "value"), [("node_id", "other"), ("title", "Other")])
def test_result_identity_must_match_the_declaration(field: str, value: str) -> None:
    changed = (
        replace(result(), node_id=value) if field == "node_id" else replace(result(), title=value)
    )
    assert evaluate([job()], archive(changed)).status is ResultStatus.FAILED


def test_failed_job_cannot_be_reclassified_as_passed() -> None:
    observed = job()
    observed["status"] = "failed"
    assert evaluate([observed], archive(result())).result is None


@pytest.mark.parametrize("allowed", [True, False])
def test_only_native_allowed_quality_failure_can_be_approved(*, allowed: bool) -> None:
    observed = job()
    observed.update(status="failed", allow_failure=allowed)
    quality = replace(result(), status=ResultStatus.FAILED, failure_kind=FailureKind.QUALITY)
    admitted = evaluate([observed], archive(quality))
    assert admitted.status is ResultStatus.FAILED
    assert (admitted.result is not None) is allowed


def test_command_failure_is_retained_as_failure() -> None:
    observed = job()
    observed["status"] = "failed"
    failed = replace(result(), status=ResultStatus.FAILED, failure_kind=FailureKind.COMMAND)
    admitted = evaluate([observed], archive(failed))
    assert admitted.status is ResultStatus.FAILED
    assert admitted.result == failed


def test_successful_job_cannot_supply_a_failed_or_unfinished_result() -> None:
    for status in (ResultStatus.WAITING, ResultStatus.IN_PROGRESS):
        assert evaluate([job()], archive(replace(result(), status=status))).result is None


@pytest.mark.parametrize("path", ["../outside.json", "/absolute.json", "bad\\path.json"])
def test_archive_traversal_is_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="unsafe"):
        archive_result(archive(result(), path), PATH)


def test_archive_file_and_size_limits_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = archive(result())
    monkeypatch.setattr(admission, "MAX_ARCHIVE_FILES", 0)
    with pytest.raises(ValueError, match="too many"):
        archive_result(payload, PATH)
    monkeypatch.setattr(admission, "MAX_ARCHIVE_FILES", 100)
    monkeypatch.setattr(admission, "MAX_EXPANDED_BYTES", 1)
    with pytest.raises(ValueError, match="expanded size"):
        archive_result(payload, PATH)


def test_missing_result_and_corrupt_archives_fail_closed() -> None:
    for payload in (None, b"not a ZIP archive", archive(result(), "other.json")):
        assert evaluate([job()], payload).status is ResultStatus.FAILED


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("success", ResultStatus.PASSED),
        ("running", ResultStatus.IN_PROGRESS),
        ("canceled", ResultStatus.CANCELLED),
        ("canceling", ResultStatus.CANCELLED),
        ("failed", ResultStatus.FAILED),
        ("manual", ResultStatus.FAILED),
    ],
)
def test_native_admission_job_is_required_and_observed(
    status: str, expected_status: ResultStatus
) -> None:
    native = {
        "id": 51,
        "name": "qg-internal:admission:mr",
        "status": status,
        "pipeline": {"id": 40, "project_id": 10, "sha": HEAD},
    }

    def respond(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=[job(), native])
            if request.url.path.endswith("/jobs")
            else httpx.Response(200, content=archive(result()))
        )

    with HttpGitLab(SERVER, "fake-token", transport=httpx.MockTransport(respond)) as api:
        observed = admit(api, expected())[admission.BOOTSTRAP_KEY]
    assert observed.status is expected_status
    assert observed.job_id == 51


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {"id": 41, "project_id": 10, "sha": HEAD},
        {"id": 40, "project_id": 11, "sha": HEAD},
        {"id": 40, "project_id": 10, "sha": "b" * 40},
    ],
)
def test_admission_job_requires_matching_pipeline_metadata(metadata: JsonValue) -> None:
    native = {
        "id": 51,
        "name": "qg-internal:admission:mr",
        "status": "success",
        "pipeline": metadata,
    }

    def respond(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=[job(), native])
            if request.url.path.endswith("/jobs")
            else httpx.Response(200, content=archive(result()))
        )

    with HttpGitLab(SERVER, "fake-token", transport=httpx.MockTransport(respond)) as api:
        assert admit(api, expected())[admission.BOOTSTRAP_KEY].status is ResultStatus.FAILED


def test_archive_limits_accept_the_exact_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    original = result()
    monkeypatch.setattr(admission, "MAX_ARCHIVE_FILES", 1)
    monkeypatch.setattr(admission, "MAX_EXPANDED_BYTES", len(original.to_json().encode()))
    assert archive_result(archive(original), PATH) == original
