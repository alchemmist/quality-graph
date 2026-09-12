from dataclasses import replace

import pytest

from quality_graph_core.adapters import AdapterContext, adapt_exit, adapt_native
from quality_graph_core.result import GitLabProvenance, Result, ResultStatus


def provenance() -> GitLabProvenance:
    return GitLabProvenance(
        "https://gitlab.example.test/nested",
        23,
        "a" * 40,
        400,
        501,
        "b" * 64,
        target_project_id=17,
        merge_request=2,
        flow_id="mr",
        operation_id="tests",
    )


def test_gitlab_result_round_trip_preserves_native_execution_identity() -> None:
    result = adapt_exit(
        AdapterContext("test", "Tests", command_succeeded=True, provenance=provenance())
    )
    assert result.schema_version == 1
    assert Result.from_json(result.to_json()) == result
    wire = result.provenance.to_value()
    assert wire["projectId"] == 23
    assert wire["targetProjectId"] == 17
    assert wire["jobId"] == 501
    assert "workflowRunId" not in wire
    assert "runAttempt" not in wire


def test_native_gitlab_result_cannot_reuse_another_job() -> None:
    context = AdapterContext("test", "Tests", command_succeeded=True, provenance=provenance())
    previous = adapt_exit(replace(context, provenance=replace(provenance(), job_id=500)))
    with pytest.raises(ValueError, match="provenance"):
        adapt_native(context, previous.to_json().encode())


def test_gitlab_branch_result_has_no_merge_request_identity() -> None:
    context = replace(provenance(), target_project_id=None, merge_request=None)
    result = Result("test", "Tests", ResultStatus.PASSED, context)
    assert Result.from_json(result.to_json()) == result
    assert "mergeRequest" not in context.to_value()


@pytest.mark.parametrize(
    "server",
    [
        "gitlab.example.test",
        "ftp://gitlab.example.test",
        "https://user:secret@host",
        "https://host/",
        "https://host?q=x",
        "https://host#fragment",
    ],
)
def test_gitlab_provenance_rejects_ambiguous_servers(server: str) -> None:
    with pytest.raises(ValueError, match="server URL"):
        replace(provenance(), server_url=server)


@pytest.mark.parametrize(
    "field", ["projectId", "pipelineId", "jobId", "targetProjectId", "mergeRequest"]
)
@pytest.mark.parametrize("value", [0, -1, True, "1"])
def test_gitlab_provenance_requires_positive_integer_identities(
    field: str, value: int | str
) -> None:
    wire = provenance().to_value()
    wire[field] = value
    with pytest.raises((TypeError, ValueError)):
        GitLabProvenance.from_value(wire)


def test_gitlab_provenance_rejects_partial_mr_and_operation_identity() -> None:
    with pytest.raises(ValueError, match="supplied together"):
        replace(provenance(), target_project_id=None)
    with pytest.raises(ValueError, match="supplied together"):
        replace(provenance(), flow_id=None)


def test_gitlab_provenance_rejects_unknown_and_github_fields() -> None:
    wire = provenance().to_value()
    wire["runAttempt"] = 1
    with pytest.raises(ValueError, match="unknown fields"):
        GitLabProvenance.from_value(wire)


def test_gitlab_result_cannot_be_relabelled_as_version_zero() -> None:
    wire = adapt_exit(
        AdapterContext("test", "Tests", command_succeeded=True, provenance=provenance())
    ).to_value()
    wire["schemaVersion"] = 0
    with pytest.raises(ValueError, match="unknown fields"):
        Result.from_value(wire)
