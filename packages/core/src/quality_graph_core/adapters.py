"""Translate command and report formats into the shared result protocol."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from quality_graph_core.json_values import JsonValidator
from quality_graph_core.result import (
    MAX_FINDINGS,
    Annotation,
    Diagnostic,
    DiagnosticKind,
    FailureKind,
    Finding,
    GitLabProvenance,
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
MAX_JUNIT_DIAGNOSTICS = 100
MAX_JUNIT_FINDINGS = MAX_FINDINGS


class AdapterError(ValueError):
    """Represent deterministic failure to read or translate a report."""


JSON = JsonValidator(AdapterError)


@dataclass(frozen=True)
class AdapterContext:
    """Provide trusted node and workflow metadata to an adapter."""

    node_id: str
    title: str
    command_succeeded: bool
    provenance: Provenance | GitLabProvenance


def adapt_exit(context: AdapterContext, output: str = "") -> Result:
    """Map one command exit outcome to a portable result."""
    status = ResultStatus.PASSED if context.command_succeeded else ResultStatus.FAILED
    failure = None if context.command_succeeded else FailureKind.COMMAND
    output = _bounded_summary(output.strip(), maximum=20_000)
    summary = "The declared command passed." if context.command_succeeded else ""
    diagnostics = (
        (Diagnostic(DiagnosticKind.COMMAND, "Command output", output[:20_000]),)
        if context.command_succeeded and output
        else ()
        if context.command_succeeded
        else (Diagnostic(DiagnosticKind.COMMAND, "The declared command failed.", output[:20_000]),)
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
        data = JSON.object(_decode_json(report, "Native result"), "native result")
        if "reportVersion" in data:
            data = _bind_producer(context, data)
        result = Result.from_value(data)
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
    try:
        return _adapt_sarif(context, report)
    except (TypeError, ValueError) as error:
        message = f"SARIF result is invalid: {error}"
        raise AdapterError(message) from error


def _adapt_sarif(context: AdapterContext, report: bytes) -> Result:
    data = _decode_json(report, "SARIF")
    root = JSON.object(data, "SARIF report")
    runs = JSON.array(root.get("runs"), "SARIF runs")
    entries: dict[str, tuple[Finding, Annotation | None]] = {}
    for run_value in runs:
        run = JSON.object(run_value, "SARIF run")
        for result_value in JSON.array(run.get("results", []), "SARIF results"):
            identity = json.dumps({"tool": run.get("tool"), "result": result_value}, sort_keys=True)
            entries[identity] = _sarif_finding(JSON.object(result_value, "SARIF result"))
    counts = Counter(finding.id for finding, _ in entries.values())
    findings: list[Finding] = []
    annotations: list[Annotation] = []
    for identity, (finding, annotation) in entries.items():
        resolved = finding
        if counts[finding.id] > 1:
            fingerprint = hashlib.sha256(identity.encode()).hexdigest()
            resolved = replace(finding, id=f"sarif-{fingerprint[:24]}", fingerprint=fingerprint)
        findings.append(resolved)
        if annotation is not None and len(findings) <= MAX_FINDINGS:
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
        tuple(findings[:MAX_FINDINGS]),
        tuple(annotations),
        omitted_findings=max(0, len(findings) - MAX_FINDINGS),
    )
    return _reconcile_command(context, result)


def adapt_junit(context: AdapterContext, report: bytes) -> Result:
    """Translate JUnit XML test failures into stable findings."""
    return adapt_native(context, json.dumps(junit_report(report)).encode())


def junit_report(report: bytes) -> dict[str, JsonValue]:
    """Normalize JUnit into provider-independent producer report data."""
    try:
        root = ElementTree.fromstring(report)
    except (ElementTree.ParseError, DefusedXmlException) as error:
        message = f"JUnit report is invalid XML: {error}"
        raise AdapterError(message) from error
    if root.tag not in {"testsuite", "testsuites"}:
        message = "JUnit report root must be testsuite or testsuites"
        raise AdapterError(message)
    cases = tuple(root.iter("testcase"))
    findings: list[Finding] = []
    diagnostics: list[JsonValue] = []
    failures = 0
    for case in cases:
        failure = case.find("failure")
        if failure is None:
            failure = case.find("error")
        if failure is None:
            continue
        failures += 1
        if len(findings) < MAX_JUNIT_FINDINGS:
            findings.append(_junit_finding(cast("XmlElement", case), cast("XmlElement", failure)))
        if len(diagnostics) < MAX_JUNIT_DIAGNOSTICS:
            diagnostics.append(
                Diagnostic(
                    DiagnosticKind.COMMAND,
                    f"{case.get('classname', '')}::{case.get('name', 'unnamed test')}"[:1_000],
                    (failure.text or failure.get("message") or "Test failed")[:20_000],
                ).to_value()
            )
    skipped = sum(case.find("skipped") is not None for case in cases)
    value: dict[str, JsonValue] = {
        "reportVersion": 0,
        "status": "failed" if findings else "passed",
        "summary": f"Ran {len(cases)} tests: {failures} failed, {skipped} skipped.",
        "metrics": [
            Metric("Tests", str(len(cases))).to_value(),
            Metric("Failures", str(failures)).to_value(),
            Metric("Skipped", str(skipped)).to_value(),
        ],
        "findings": [finding.to_value() for finding in findings],
        "diagnostics": diagnostics,
        "notes": [f"{failures - MAX_JUNIT_DIAGNOSTICS} additional test traces omitted."]
        if failures > MAX_JUNIT_DIAGNOSTICS
        else [],
    }
    if failures > MAX_JUNIT_FINDINGS:
        value["omittedFindings"] = failures - MAX_JUNIT_FINDINGS
        notes = cast("list[JsonValue]", value["notes"])
        notes.append(f"{failures - MAX_JUNIT_FINDINGS} additional findings omitted.")
    if findings:
        value["failureKind"] = "quality"
    return value


def _bind_producer(context: AdapterContext, data: dict[str, JsonValue]) -> dict[str, JsonValue]:
    owned = {"nodeId", "title", "provenance", "controls", "schemaVersion"}
    if owned & data.keys():
        message = "producer report must not supply framework-owned fields"
        raise AdapterError(message)
    value = dict(data)
    version = value.pop("reportVersion")
    if type(version) is not int or version != 0:
        message = "unsupported producer report version"
        raise AdapterError(message)
    value["schemaVersion"] = int(isinstance(context.provenance, GitLabProvenance))
    value.update(
        nodeId=context.node_id, title=context.title, provenance=context.provenance.to_value()
    )
    if value.get("status") not in {"passed", "failed", "skipped", "cancelled"}:
        message = "producer report must have a terminal status"
        raise AdapterError(message)
    return value


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
    if (
        context.command_succeeded
        and result.omitted_findings
        and result.status is ResultStatus.PASSED
    ):
        result = replace(result, status=ResultStatus.FAILED, failure_kind=FailureKind.QUALITY)
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


def _bounded_summary(value: str, *, maximum: int = MAX_SUMMARY_CHARACTERS) -> str:
    if len(value) <= maximum:
        return value
    omitted = len(value) - maximum
    while True:
        notice = f"\n\n_Output truncated; {omitted} characters omitted._"
        prefix_length = maximum - len(notice)
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
    rule_id = JSON.optional_string(data.get("ruleId"), "SARIF rule id")
    message = _sarif_message(JSON.object(data.get("message"), "SARIF message"))
    severity = _sarif_severity(JSON.optional_string(data.get("level"), "SARIF level"))
    location = _sarif_location(data.get("locations"))
    partial = JSON.optional_object(data.get("partialFingerprints"), "SARIF partial fingerprints")
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
    return JSON.string(text, "SARIF message text")


def _sarif_severity(value: str | None) -> Severity:
    if value == "error":
        return Severity.ERROR
    if value == "warning":
        return Severity.WARNING
    return Severity.NOTICE


def _sarif_location(value: JsonValue) -> SourceLocation | None:
    locations = JSON.array(value if value is not None else [], "SARIF locations")
    if not locations:
        return None
    location = JSON.object(locations[0], "SARIF location")
    physical = JSON.object(location.get("physicalLocation"), "SARIF physical location")
    artifact = JSON.object(physical.get("artifactLocation"), "SARIF artifact location")
    region = JSON.object(physical.get("region"), "SARIF region")
    start_line = JSON.integer(region.get("startLine"), "SARIF start line")
    end_line = JSON.optional_integer(region.get("endLine"), "SARIF end line") or start_line
    return SourceLocation(
        JSON.string(artifact.get("uri"), "SARIF artifact URI"),
        start_line,
        end_line,
        JSON.optional_integer(region.get("startColumn"), "SARIF start column"),
        JSON.optional_integer(region.get("endColumn"), "SARIF end column"),
    )


def _sarif_fingerprint(
    rule_id: str | None,
    message: str,
    location: SourceLocation | None,
    partial: dict[str, JsonValue] | None,
) -> str:
    if partial:
        semantic = "\n".join(
            f"{key}={JSON.string(value, 'SARIF fingerprint')}"
            for key, value in sorted(partial.items())
        )
    else:
        semantic = "\n".join((rule_id or "", message, location.path if location else ""))
    return hashlib.sha256(semantic.encode()).hexdigest()


def _junit_finding(case: XmlElement, failure: XmlElement) -> Finding:
    class_name = case.get("classname", "")
    test_name = case.get("name", "unnamed test")
    failure_type = failure.get("type", "failure")
    detail = failure.get("message") or (failure.text or "Test failed").strip()
    message = f"{class_name}::{test_name}: {detail}"[:1_000]
    semantic = f"{class_name}\n{test_name}\n{failure_type}\n{message}"
    fingerprint = hashlib.sha256(semantic.encode()).hexdigest()
    return Finding(
        f"junit-{fingerprint[:24]}",
        Severity.ERROR,
        message,
        failure_type,
        fingerprint=fingerprint,
        group=class_name or None,
        location=SourceLocation(case.get("file"), 1, 1) if case.get("file") else None,
    )
