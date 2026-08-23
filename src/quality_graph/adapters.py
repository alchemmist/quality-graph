"""Translate command and report formats into the shared result protocol."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from defusedxml import ElementTree

from quality_graph.result import (
    Annotation,
    Diagnostic,
    DiagnosticKind,
    FailureKind,
    Finding,
    JsonValue,
    Metric,
    Provenance,
    Result,
    ResultStatus,
    Severity,
    SourceLocation,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from typing import Protocol

    class XmlElement(Protocol):
        """Describe the defused XML element operations used by the adapter."""

        tag: str
        text: str | None

        def iter(self, tag: str) -> Iterator[XmlElement]:
            """Iterate descendants with the requested tag."""
            ...

        def find(self, path: str) -> XmlElement | None:
            """Find the first matching descendant."""
            ...

        def get(self, key: str, _default: str = "") -> str:
            """Return one XML attribute or its default."""
            ...


MAX_REPORT_BYTES = 10 * 1024 * 1024
MAX_SUMMARY_CHARACTERS = 60_000


class AdapterError(ValueError):
    """Represent deterministic failure to read or translate a report."""


@dataclass(frozen=True)
class AdapterContext:
    """Provide trusted node and workflow metadata to an adapter."""

    node_id: str
    title: str
    command_succeeded: bool
    provenance: Provenance


def adapt_exit(context: AdapterContext, output: str = "") -> Result:
    """Map one command exit outcome to a portable result."""
    status = ResultStatus.PASSED if context.command_succeeded else ResultStatus.FAILED
    failure = None if context.command_succeeded else FailureKind.COMMAND
    summary = _bounded_summary(output.strip())
    diagnostics = (
        ()
        if context.command_succeeded
        else (
            Diagnostic(
                DiagnosticKind.COMMAND,
                "The declared command failed.",
                summary[:20_000],
            ),
        )
    )
    return Result(
        context.node_id,
        context.title,
        status,
        context.provenance,
        failure,
        summary,
        diagnostics=diagnostics,
    )


def adapt_native(context: AdapterContext, report: bytes) -> Result:
    """Validate a native result and bind it to trusted execution metadata."""
    try:
        result = Result.from_json(report)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        message = f"Native result is invalid: {error}"
        raise AdapterError(message) from error
    if result.node_id != context.node_id or result.title != context.title:
        message = "Native result identity does not match the declared node"
        raise AdapterError(message)
    if result.provenance != context.provenance:
        message = "Native result provenance does not match the current workflow attempt"
        raise AdapterError(message)
    return _reconcile_command(context, result)


def adapt_sarif(context: AdapterContext, report: bytes) -> Result:
    """Translate SARIF findings and locations into the shared protocol."""
    data = _decode_json(report, "SARIF")
    root = _object(data, "SARIF report")
    runs = _array(root.get("runs"), "SARIF runs")
    findings: list[Finding] = []
    annotations: list[Annotation] = []
    for run_value in runs:
        run = _object(run_value, "SARIF run")
        for result_value in _array(run.get("results", []), "SARIF results"):
            finding, annotation = _sarif_finding(_object(result_value, "SARIF result"))
            findings.append(finding)
            if annotation is not None:
                annotations.append(annotation)
    errors = sum(finding.severity is Severity.ERROR for finding in findings)
    status = ResultStatus.FAILED if errors or not context.command_succeeded else ResultStatus.PASSED
    result = Result(
        context.node_id,
        context.title,
        status,
        context.provenance,
        FailureKind.QUALITY if status is ResultStatus.FAILED else None,
        f"Found {len(findings)} SARIF findings ({errors} errors).",
        (Metric("Findings", str(len(findings))), Metric("Errors", str(errors))),
        tuple(findings),
        tuple(annotations),
    )
    return _reconcile_command(context, result)


def adapt_junit(context: AdapterContext, report: bytes) -> Result:
    """Translate JUnit XML test failures into stable findings."""
    try:
        root = ElementTree.fromstring(report)
    except ElementTree.ParseError as error:
        message = f"JUnit report is invalid XML: {error}"
        raise AdapterError(message) from error
    if root.tag not in {"testsuite", "testsuites"}:
        message = "JUnit report root must be testsuite or testsuites"
        raise AdapterError(message)
    cases = tuple(root.iter("testcase"))
    findings = tuple(
        finding
        for case in cases
        for finding in (_junit_finding(cast("XmlElement", case)),)
        if finding is not None
    )
    skipped = sum(case.find("skipped") is not None for case in cases)
    status = (
        ResultStatus.FAILED if findings or not context.command_succeeded else ResultStatus.PASSED
    )
    result = Result(
        context.node_id,
        context.title,
        status,
        context.provenance,
        FailureKind.QUALITY if status is ResultStatus.FAILED else None,
        f"Ran {len(cases)} tests: {len(findings)} failed, {skipped} skipped.",
        (
            Metric("Tests", str(len(cases))),
            Metric("Failures", str(len(findings))),
            Metric("Skipped", str(skipped)),
        ),
        findings,
    )
    return _reconcile_command(context, result)


def read_report(workspace: Path, relative_path: str) -> bytes:
    """Read one bounded report without escaping the repository workspace."""
    root = workspace.resolve()
    path = (root / relative_path).resolve()
    if path != root and root not in path.parents:
        message = f"Report path escapes the workspace: {relative_path}"
        raise AdapterError(message)
    if not path.is_file():
        message = f"Report file does not exist: {relative_path}"
        raise AdapterError(message)
    size = path.stat().st_size
    if size > MAX_REPORT_BYTES:
        message = f"Report exceeds the {MAX_REPORT_BYTES}-byte limit: {relative_path}"
        raise AdapterError(message)
    return path.read_bytes()


def adapter_failure(context: AdapterContext, error: AdapterError) -> Result:
    """Represent adapter failure distinctly from a check failure."""
    return Result(
        context.node_id,
        context.title,
        ResultStatus.FAILED,
        context.provenance,
        FailureKind.ADAPTER,
        str(error),
        diagnostics=(Diagnostic(DiagnosticKind.ADAPTER, "Result adapter failed.", str(error)),),
    )


def _reconcile_command(context: AdapterContext, result: Result) -> Result:
    if context.command_succeeded or result.status in {ResultStatus.FAILED, ResultStatus.CANCELLED}:
        return result
    diagnostic = Diagnostic(
        DiagnosticKind.COMMAND,
        "The declared command failed despite a passing report.",
    )
    return replace(
        result,
        status=ResultStatus.FAILED,
        failure_kind=FailureKind.COMMAND,
        diagnostics=(*result.diagnostics, diagnostic),
    )


def _bounded_summary(value: str) -> str:
    if len(value) <= MAX_SUMMARY_CHARACTERS:
        return value
    omitted = len(value) - MAX_SUMMARY_CHARACTERS
    while True:
        notice = f"\n\n_Output truncated; {omitted} characters omitted._"
        prefix_length = MAX_SUMMARY_CHARACTERS - len(notice)
        updated = len(value) - prefix_length
        if updated == omitted:
            return value[:prefix_length] + notice
        omitted = updated


def _decode_json(value: bytes, context: str) -> JsonValue:
    try:
        return cast("JsonValue", json.loads(value))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        message = f"{context} is invalid JSON: {error}"
        raise AdapterError(message) from error


def _sarif_finding(data: dict[str, JsonValue]) -> tuple[Finding, Annotation | None]:
    rule_id = _optional_string(data.get("ruleId"), "SARIF rule id")
    message = _sarif_message(_object(data.get("message"), "SARIF message"))
    severity = _sarif_severity(_optional_string(data.get("level"), "SARIF level"))
    location = _sarif_location(data.get("locations"))
    partial = _optional_object(data.get("partialFingerprints"), "SARIF partial fingerprints")
    fingerprint = _sarif_fingerprint(rule_id, message, location, partial)
    finding_id = f"sarif-{fingerprint[:24]}"
    finding = Finding(
        finding_id,
        severity,
        message,
        rule_id,
        fingerprint=fingerprint,
        location=location,
    )
    annotation = Annotation(severity, message, location, rule_id) if location is not None else None
    return finding, annotation


def _sarif_message(data: dict[str, JsonValue]) -> str:
    text = data.get("text", data.get("markdown"))
    return _string(text, "SARIF message text")


def _sarif_severity(value: str | None) -> Severity:
    if value == "error":
        return Severity.ERROR
    if value == "warning":
        return Severity.WARNING
    return Severity.NOTICE


def _sarif_location(value: JsonValue) -> SourceLocation | None:
    locations = _array(value if value is not None else [], "SARIF locations")
    if not locations:
        return None
    location = _object(locations[0], "SARIF location")
    physical = _object(location.get("physicalLocation"), "SARIF physical location")
    artifact = _object(physical.get("artifactLocation"), "SARIF artifact location")
    region = _object(physical.get("region"), "SARIF region")
    start_line = _integer(region.get("startLine"), "SARIF start line")
    end_line = _optional_integer(region.get("endLine"), "SARIF end line") or start_line
    return SourceLocation(
        _string(artifact.get("uri"), "SARIF artifact URI"),
        start_line,
        end_line,
        _optional_integer(region.get("startColumn"), "SARIF start column"),
        _optional_integer(region.get("endColumn"), "SARIF end column"),
    )


def _sarif_fingerprint(
    rule_id: str | None,
    message: str,
    location: SourceLocation | None,
    partial: dict[str, JsonValue] | None,
) -> str:
    if partial:
        semantic = "\n".join(
            f"{key}={_string(value, 'SARIF fingerprint')}" for key, value in sorted(partial.items())
        )
    else:
        semantic = "\n".join((rule_id or "", message, location.path if location else ""))
    return hashlib.sha256(semantic.encode()).hexdigest()


def _junit_finding(case: XmlElement) -> Finding | None:
    failure = case.find("failure")
    if failure is None:
        failure = case.find("error")
    if failure is None:
        return None
    class_name = case.get("classname", "")
    test_name = case.get("name", "unnamed test")
    failure_type = failure.get("type", "failure")
    message = failure.get("message") or (failure.text or "Test failed").strip()
    semantic = f"{class_name}\n{test_name}\n{failure_type}\n{message}"
    fingerprint = hashlib.sha256(semantic.encode()).hexdigest()
    return Finding(
        f"junit-{fingerprint[:24]}",
        Severity.ERROR,
        message,
        failure_type,
        fingerprint=fingerprint,
        group=class_name or None,
    )


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise AdapterError(message)
    return value


def _optional_object(value: JsonValue, context: str) -> dict[str, JsonValue] | None:
    return None if value is None else _object(value, context)


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise AdapterError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise AdapterError(message)
    return value


def _optional_string(value: JsonValue, context: str) -> str | None:
    return None if value is None else _string(value, context)


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise AdapterError(message)
    return value


def _optional_integer(value: JsonValue, context: str) -> int | None:
    return None if value is None else _integer(value, context)
