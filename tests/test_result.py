import json

import pytest

from quality_graph_core.result import (
    Annotation,
    Control,
    ControlKind,
    Diagnostic,
    DiagnosticKind,
    FailureKind,
    Finding,
    Metric,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)


def provenance() -> Provenance:
    return Provenance(
        repository="owner/repository",
        pull_request=42,
        head_sha="a" * 40,
        workflow_run_id=100,
        run_attempt=2,
        graph_digest="b" * 64,
    )


def test_result_round_trips_as_deterministic_camel_case_json() -> None:
    location = SourceLocation(
        path="src/app.py",
        start_line=3,
        end_line=3,
        start_column=2,
        end_column=4,
    )
    result = Result(
        node_id="lint",
        title="Lint",
        status=ResultStatus.FAILED,
        failure_kind=FailureKind.QUALITY,
        summary="Found a violation",
        metrics=(Metric(label="Findings", value="1"),),
        findings=(
            Finding(
                id="ruff-f401-import",
                severity=Severity.ERROR,
                message="Unused import",
                rule_id="F401",
                documentation_url="https://example.com/rules/F401",
                fingerprint="semantic-import",
                location=location,
                group="imports",
            ),
        ),
        annotations=(
            Annotation(
                level=Severity.ERROR,
                message="Unused import",
                location=location,
                title="F401",
            ),
        ),
        diagnostics=(
            Diagnostic(kind=DiagnosticKind.COMMAND, message="Command failed", detail="exit 1"),
        ),
        controls=(Control(kind=ControlKind.FINDING, target="ruff-f401-import"),),
        notes=("Approval is available to administrators.",),
        provenance=provenance(),
    )

    serialized = result.to_json()

    assert serialized.endswith("\n")
    assert '"nodeId": "lint"' in serialized
    assert Result.from_json(serialized) == result
    assert json.loads(serialized)["schemaVersion"] == 0


@pytest.mark.parametrize("path", ["/outside/result.py", "../result.py", "src\\result.py"])
def test_source_locations_reject_paths_outside_the_repository(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        SourceLocation(path=path, start_line=1, end_line=1)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"start_line": 2, "end_line": 1}, "positive and ordered"),
        ({"start_line": 1, "end_line": 1, "start_column": 2}, "provided together"),
        (
            {"start_line": 1, "end_line": 2, "start_column": 1, "end_column": 2},
            "one positive ordered source line",
        ),
    ],
)
def test_source_locations_reject_invalid_ranges(values: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SourceLocation(path="src/app.py", **values)


def test_result_requires_failure_kind_exactly_for_failures() -> None:
    with pytest.raises(ValueError, match="exactly for failed"):
        Result(
            node_id="test",
            title="Tests",
            status=ResultStatus.FAILED,
            provenance=provenance(),
        )
    with pytest.raises(ValueError, match="exactly for failed"):
        Result(
            node_id="test",
            title="Tests",
            status=ResultStatus.PASSED,
            failure_kind=FailureKind.QUALITY,
            provenance=provenance(),
        )


def test_minimal_passed_result_omits_optional_wire_fields() -> None:
    result = Result(
        node_id="test",
        title="Tests",
        status=ResultStatus.PASSED,
        provenance=Provenance(
            repository="owner/repository",
            head_sha="a" * 40,
            workflow_run_id=1,
            run_attempt=1,
            graph_digest="b" * 64,
        ),
        findings=(
            Finding(
                id="finding",
                severity=Severity.NOTICE,
                message="Notice",
                location=SourceLocation(path="src/app.py", start_line=1, end_line=1),
            ),
        ),
    )

    value = result.to_value()

    assert "failureKind" not in value
    assert "pullRequest" not in result.provenance.to_value()
    assert result.findings[0].location.to_value() == {
        "path": "src/app.py",
        "startLine": 1,
        "endLine": 1,
    }


def test_result_rejects_duplicate_finding_identifiers() -> None:
    finding = Finding(id="duplicate", severity=Severity.ERROR, message="Failure")
    with pytest.raises(ValueError, match="must be unique"):
        Result(
            node_id="test",
            title="Tests",
            status=ResultStatus.FAILED,
            failure_kind=FailureKind.QUALITY,
            findings=(finding, finding),
            provenance=provenance(),
        )


def test_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        Result.from_json(
            json.dumps(
                {
                    "schemaVersion": 0,
                    "nodeId": "lint",
                    "title": "Lint",
                    "status": "passed",
                    "provenance": provenance().to_value(),
                    "unknown": True,
                }
            )
        )


def test_finding_validation_reports_invalid_identity() -> None:
    with pytest.raises(ValueError, match="invalid finding identifier"):
        Finding(id="INVALID ID", severity=Severity.ERROR, message="Failure")


def test_finding_rejects_non_http_documentation_url() -> None:
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        Finding(
            id="invalid-url",
            severity=Severity.ERROR,
            message="Failure",
            documentation_url="javascript:alert(1)",
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"repository": "invalid"}, "repository identity"),
        ({"head_sha": "abc"}, "head SHA"),
        ({"workflow_run_id": -1}, "run and attempt"),
        ({"run_attempt": 0}, "run and attempt"),
        ({"pull_request": 0}, "pull request"),
        ({"graph_digest": "abc"}, "graph digest"),
    ],
)
def test_provenance_rejects_invalid_identity(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "repository": "owner/repository",
        "head_sha": "a" * 40,
        "workflow_run_id": 1,
        "run_attempt": 1,
        "graph_digest": "b" * 64,
        "pull_request": 1,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        Provenance(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema_version": 1}, "unsupported"),
        ({"node_id": "INVALID"}, "node identifier"),
        ({"title": ""}, "result title length"),
        ({"notes": ("",)}, "result note length"),
        (
            {"metrics": tuple(Metric(label="Metric", value="1") for _ in range(101))},
            "metrics must contain",
        ),
    ],
)
def test_result_rejects_invalid_bounds(changes: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "node_id": "test",
        "title": "Tests",
        "status": ResultStatus.PASSED,
        "provenance": provenance(),
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        Result(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("payload", "error", "message"),
    [
        ("[]", TypeError, "result must be an object"),
        (
            '{"schemaVersion":0,"nodeId":1,"title":"Test","status":"passed","provenance":{}}',
            TypeError,
            "node id must be a string",
        ),
        (
            '{"schemaVersion":false,"nodeId":"test","title":"Test","status":"passed",'
            '"provenance":{"repository":"owner/repo","headSha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"workflowRunId":1,"runAttempt":1,'
            '"graphDigest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}',
            TypeError,
            "schema version must be an integer",
        ),
        (
            '{"schemaVersion":0,"nodeId":"test","title":"Test","status":"passed",'
            '"metrics":{},"provenance":{"repository":"owner/repo",'
            '"headSha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","workflowRunId":1,'
            '"runAttempt":1,"graphDigest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}',
            TypeError,
            "metrics must be an array",
        ),
    ],
)
def test_result_narrows_untrusted_json(payload: str, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        Result.from_json(payload)


def test_nested_protocol_models_reject_invalid_wire_values() -> None:
    with pytest.raises(TypeError, match="control checked must be a boolean"):
        Control.from_value({"kind": "finding", "target": "finding", "checked": "yes"})
    with pytest.raises(ValueError, match="unknown fields"):
        Diagnostic.from_value(
            {"kind": "adapter", "message": "Failure", "detail": "", "unknown": True}
        )
