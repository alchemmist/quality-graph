"""Versioned portable result protocol for Quality Graph nodes."""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Self, cast

type JsonScalar = bool | float | int | None | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
FINDING_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,254}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ResultStatus(StrEnum):
    """Represent a node lifecycle state."""

    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class FailureKind(StrEnum):
    """Distinguish quality failures from framework failures."""

    QUALITY = "quality"
    COMMAND = "command"
    ADAPTER = "adapter"
    PROTOCOL = "protocol"
    CANCELLATION = "cancellation"
    INFRASTRUCTURE = "infrastructure"


class Severity(StrEnum):
    """Represent finding and annotation severity."""

    NOTICE = "notice"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticKind(StrEnum):
    """Identify the subsystem that produced a diagnostic."""

    COMMAND = "command"
    ADAPTER = "adapter"
    PROTOCOL = "protocol"
    INFRASTRUCTURE = "infrastructure"


class ControlKind(StrEnum):
    """Represent an approval target exposed by a result."""

    FINDING = "finding"
    FILE = "file"
    NODE = "node"


@dataclass(frozen=True)
class Metric:
    """Store one ordered human-readable measurement."""

    label: str
    value: str

    def __post_init__(self) -> None:
        """Validate metric rendering bounds."""
        _bounded_text(self.label, "metric label", minimum=1, maximum=100)
        _bounded_text(self.value, "metric value", maximum=500)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the metric into the protocol JSON domain."""
        return {"label": self.label, "value": self.value}

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse a metric from untrusted JSON."""
        data = _object(value, "metric")
        _reject_unknown(data, {"label", "value"}, "metric")
        return cls(
            _string(data.get("label"), "metric label"),
            _string(data.get("value"), "metric value"),
        )


@dataclass(frozen=True)
class SourceLocation:
    """Describe a trustworthy repository-relative source range."""

    path: str
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None

    def __post_init__(self) -> None:
        """Validate the repository path and source range."""
        _bounded_text(self.path, "source path", minimum=1, maximum=4_096)
        source_path = PurePosixPath(self.path)
        if source_path.is_absolute() or ".." in source_path.parts or "\\" in self.path:
            message = "source path must be repository-relative"
            raise ValueError(message)
        if self.start_line < 1 or self.end_line < self.start_line:
            message = "source line range must be positive and ordered"
            raise ValueError(message)
        if (self.start_column is None) != (self.end_column is None):
            message = "source columns must be provided together"
            raise ValueError(message)
        if (
            self.start_column is not None
            and self.end_column is not None
            and (
                self.start_line != self.end_line
                or self.start_column < 1
                or self.end_column < self.start_column
            )
        ):
            message = "source columns require one positive ordered source line"
            raise ValueError(message)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the source range into the protocol JSON domain."""
        value: dict[str, JsonValue] = {
            "path": self.path,
            "startLine": self.start_line,
            "endLine": self.end_line,
        }
        if self.start_column is not None:
            value["startColumn"] = self.start_column
            value["endColumn"] = self.end_column
        return value

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse a source range from untrusted JSON."""
        data = _object(value, "source location")
        _reject_unknown(
            data,
            {"path", "startLine", "endLine", "startColumn", "endColumn"},
            "source location",
        )
        return cls(
            _string(data.get("path"), "source path"),
            _integer(data.get("startLine"), "source start line"),
            _integer(data.get("endLine"), "source end line"),
            _optional_integer(data.get("startColumn"), "source start column"),
            _optional_integer(data.get("endColumn"), "source end column"),
        )


@dataclass(frozen=True)
class Finding:
    """Represent one stable, individually approvable observation."""

    id: str
    severity: Severity
    message: str
    rule_id: str | None = None
    documentation_url: str | None = None
    fingerprint: str | None = None
    location: SourceLocation | None = None
    group: str | None = None

    def __post_init__(self) -> None:
        """Validate finding identity, content, and link bounds."""
        if FINDING_IDENTIFIER_RE.fullmatch(self.id) is None:
            message = f"invalid finding identifier: {self.id}"
            raise ValueError(message)
        _bounded_text(self.message, "finding message", minimum=1, maximum=1_000)
        _optional_bounded(self.rule_id, "finding rule", maximum=255)
        _optional_bounded(self.fingerprint, "finding fingerprint", maximum=512)
        _optional_bounded(self.group, "finding group", maximum=255)
        if self.documentation_url is not None:
            parsed = urllib.parse.urlparse(self.documentation_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                message = "finding documentation URL must use HTTP or HTTPS"
                raise ValueError(message)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the finding into the protocol JSON domain."""
        value: dict[str, JsonValue] = {
            "id": self.id,
            "severity": self.severity.value,
            "message": self.message,
        }
        _put_optional(value, "ruleId", self.rule_id)
        _put_optional(value, "documentationUrl", self.documentation_url)
        _put_optional(value, "fingerprint", self.fingerprint)
        _put_optional(value, "location", self.location.to_value() if self.location else None)
        _put_optional(value, "group", self.group)
        return value

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse a finding from untrusted JSON."""
        data = _object(value, "finding")
        known = {
            "id",
            "severity",
            "message",
            "ruleId",
            "documentationUrl",
            "fingerprint",
            "location",
            "group",
        }
        _reject_unknown(data, known, "finding")
        location = data.get("location")
        return cls(
            _string(data.get("id"), "finding id"),
            Severity(_string(data.get("severity"), "finding severity")),
            _string(data.get("message"), "finding message"),
            _optional_string(data.get("ruleId"), "finding rule"),
            _optional_string(data.get("documentationUrl"), "finding documentation URL"),
            _optional_string(data.get("fingerprint"), "finding fingerprint"),
            SourceLocation.from_value(location) if location is not None else None,
            _optional_string(data.get("group"), "finding group"),
        )


@dataclass(frozen=True)
class Annotation:
    """Represent one GitHub source annotation."""

    level: Severity
    message: str
    location: SourceLocation
    title: str | None = None

    def __post_init__(self) -> None:
        """Validate annotation rendering bounds."""
        _bounded_text(self.message, "annotation message", minimum=1, maximum=1_000)
        _optional_bounded(self.title, "annotation title", maximum=255)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the annotation into the protocol JSON domain."""
        value: dict[str, JsonValue] = {
            "level": self.level.value,
            "message": self.message,
            "location": self.location.to_value(),
        }
        _put_optional(value, "title", self.title)
        return value

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse an annotation from untrusted JSON."""
        data = _object(value, "annotation")
        _reject_unknown(data, {"level", "message", "location", "title"}, "annotation")
        return cls(
            Severity(_string(data.get("level"), "annotation level")),
            _string(data.get("message"), "annotation message"),
            SourceLocation.from_value(data.get("location")),
            _optional_string(data.get("title"), "annotation title"),
        )


@dataclass(frozen=True)
class Diagnostic:
    """Describe an actionable command or framework failure."""

    kind: DiagnosticKind
    message: str
    detail: str = ""

    def __post_init__(self) -> None:
        """Validate diagnostic rendering bounds."""
        _bounded_text(self.message, "diagnostic message", minimum=1, maximum=1_000)
        _bounded_text(self.detail, "diagnostic detail", maximum=20_000)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the diagnostic into the protocol JSON domain."""
        return {"kind": self.kind.value, "message": self.message, "detail": self.detail}

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse a diagnostic from untrusted JSON."""
        data = _object(value, "diagnostic")
        _reject_unknown(data, {"kind", "message", "detail"}, "diagnostic")
        return cls(
            DiagnosticKind(_string(data.get("kind"), "diagnostic kind")),
            _string(data.get("message"), "diagnostic message"),
            _string(data.get("detail", ""), "diagnostic detail"),
        )


@dataclass(frozen=True)
class Control:
    """Describe a semantic reversible control without executable command text."""

    kind: ControlKind
    target: str
    checked: bool = False

    def __post_init__(self) -> None:
        """Validate the semantic control target."""
        _bounded_text(self.target, "control target", minimum=1, maximum=4_096)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the control into the protocol JSON domain."""
        return {"kind": self.kind.value, "target": self.target, "checked": self.checked}

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse a control from untrusted JSON."""
        data = _object(value, "control")
        _reject_unknown(data, {"kind", "target", "checked"}, "control")
        return cls(
            ControlKind(_string(data.get("kind"), "control kind")),
            _string(data.get("target"), "control target"),
            _boolean(data.get("checked", False), "control checked"),
        )


@dataclass(frozen=True)
class Provenance:
    """Bind a result to one repository workflow attempt."""

    repository: str
    head_sha: str
    workflow_run_id: int
    run_attempt: int
    graph_digest: str
    pull_request: int | None = None

    def __post_init__(self) -> None:
        """Validate workflow identity and attempt provenance."""
        if REPOSITORY_RE.fullmatch(self.repository) is None:
            message = f"invalid repository identity: {self.repository}"
            raise ValueError(message)
        if GIT_SHA_RE.fullmatch(self.head_sha) is None:
            message = "head SHA must contain 40 or 64 lowercase hexadecimal characters"
            raise ValueError(message)
        if self.workflow_run_id < 0 or self.run_attempt < 1:
            message = "workflow run and attempt must be positive"
            raise ValueError(message)
        if self.pull_request is not None and self.pull_request < 1:
            message = "pull request number must be positive"
            raise ValueError(message)
        if DIGEST_RE.fullmatch(self.graph_digest) is None:
            message = "graph digest must contain 64 lowercase hexadecimal characters"
            raise ValueError(message)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize provenance into the protocol JSON domain."""
        value: dict[str, JsonValue] = {
            "repository": self.repository,
            "headSha": self.head_sha,
            "workflowRunId": self.workflow_run_id,
            "runAttempt": self.run_attempt,
            "graphDigest": self.graph_digest,
        }
        _put_optional(value, "pullRequest", self.pull_request)
        return value

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse provenance from untrusted JSON."""
        data = _object(value, "provenance")
        known = {
            "repository",
            "pullRequest",
            "headSha",
            "workflowRunId",
            "runAttempt",
            "graphDigest",
        }
        _reject_unknown(data, known, "provenance")
        return cls(
            _string(data.get("repository"), "repository"),
            _string(data.get("headSha"), "head SHA"),
            _integer(data.get("workflowRunId"), "workflow run id"),
            _integer(data.get("runAttempt"), "run attempt"),
            _string(data.get("graphDigest"), "graph digest"),
            _optional_integer(data.get("pullRequest"), "pull request"),
        )


@dataclass(frozen=True)
class Result:
    """Carry one node's complete portable Quality Graph result."""

    node_id: str
    title: str
    status: ResultStatus
    provenance: Provenance
    failure_kind: FailureKind | None = None
    summary: str = ""
    metrics: tuple[Metric, ...] = ()
    findings: tuple[Finding, ...] = ()
    annotations: tuple[Annotation, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    controls: tuple[Control, ...] = ()
    notes: tuple[str, ...] = ()
    schema_version: int = 0

    def __post_init__(self) -> None:
        """Validate result identity, bounds, and lifecycle consistency."""
        if self.schema_version != 0:
            message = f"unsupported result schema version: {self.schema_version}"
            raise ValueError(message)
        if IDENTIFIER_RE.fullmatch(self.node_id) is None:
            message = f"invalid node identifier: {self.node_id}"
            raise ValueError(message)
        _bounded_text(self.title, "result title", minimum=1, maximum=255)
        _bounded_text(self.summary, "result summary", maximum=200_000)
        _bounded_collection(self.metrics, "metrics", 100)
        _bounded_collection(self.findings, "findings", 10_000)
        _bounded_collection(self.annotations, "annotations", 10_000)
        _bounded_collection(self.diagnostics, "diagnostics", 100)
        _bounded_collection(self.controls, "controls", 10_000)
        _bounded_collection(self.notes, "notes", 100)
        for note in self.notes:
            _bounded_text(note, "result note", minimum=1, maximum=1_000)
        failed = self.status in {ResultStatus.FAILED, ResultStatus.CANCELLED}
        if failed != (self.failure_kind is not None):
            message = "failure kind must be present exactly for failed results"
            raise ValueError(message)
        if len({finding.id for finding in self.findings}) != len(self.findings):
            message = "finding identifiers must be unique within a result"
            raise ValueError(message)

    def to_value(self) -> dict[str, JsonValue]:
        """Serialize the result into the protocol JSON domain."""
        value: dict[str, JsonValue] = {
            "schemaVersion": self.schema_version,
            "nodeId": self.node_id,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "metrics": [metric.to_value() for metric in self.metrics],
            "findings": [finding.to_value() for finding in self.findings],
            "annotations": [annotation.to_value() for annotation in self.annotations],
            "diagnostics": [diagnostic.to_value() for diagnostic in self.diagnostics],
            "controls": [control.to_value() for control in self.controls],
            "notes": list(self.notes),
            "provenance": self.provenance.to_value(),
        }
        failure_kind = self.failure_kind.value if self.failure_kind else None
        _put_optional(value, "failureKind", failure_kind)
        return value

    @classmethod
    def from_value(cls, value: JsonValue) -> Self:
        """Parse a result from untrusted JSON."""
        data = _object(value, "result")
        known = {
            "schemaVersion",
            "nodeId",
            "title",
            "status",
            "failureKind",
            "summary",
            "metrics",
            "findings",
            "annotations",
            "diagnostics",
            "controls",
            "notes",
            "provenance",
        }
        _reject_unknown(data, known, "result")
        failure_kind = _optional_string(data.get("failureKind"), "failure kind")
        return cls(
            _string(data.get("nodeId"), "node id"),
            _string(data.get("title"), "result title"),
            ResultStatus(_string(data.get("status"), "result status")),
            Provenance.from_value(data.get("provenance")),
            FailureKind(failure_kind) if failure_kind is not None else None,
            _string(data.get("summary", ""), "result summary"),
            tuple(Metric.from_value(item) for item in _array(data.get("metrics", []), "metrics")),
            tuple(
                Finding.from_value(item) for item in _array(data.get("findings", []), "findings")
            ),
            tuple(
                Annotation.from_value(item)
                for item in _array(data.get("annotations", []), "annotations")
            ),
            tuple(
                Diagnostic.from_value(item)
                for item in _array(data.get("diagnostics", []), "diagnostics")
            ),
            tuple(
                Control.from_value(item) for item in _array(data.get("controls", []), "controls")
            ),
            tuple(_string(item, "result note") for item in _array(data.get("notes", []), "notes")),
            _integer(data.get("schemaVersion"), "schema version"),
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> Self:
        """Parse and validate a result from JSON."""
        return cls.from_value(cast("JsonValue", json.loads(value)))

    def to_json(self) -> str:
        """Serialize the result as deterministic canonical JSON."""
        return json.dumps(self.to_value(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
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


def _boolean(value: JsonValue, context: str) -> bool:
    if not isinstance(value, bool):
        message = f"{context} must be a boolean"
        raise TypeError(message)
    return value


def _bounded_text(value: str, context: str, *, minimum: int = 0, maximum: int) -> None:
    if not minimum <= len(value) <= maximum:
        message = f"{context} length must be between {minimum} and {maximum}"
        raise ValueError(message)


def _optional_bounded(value: str | None, context: str, *, maximum: int) -> None:
    if value is not None:
        _bounded_text(value, context, maximum=maximum)


def _bounded_collection(value: tuple[object, ...], context: str, maximum: int) -> None:
    if len(value) > maximum:
        message = f"{context} must contain at most {maximum} items"
        raise ValueError(message)


def _reject_unknown(data: dict[str, JsonValue], known: set[str], context: str) -> None:
    unknown = sorted(data.keys() - known)
    if unknown:
        message = f"{context} contains unknown fields: {', '.join(unknown)}"
        raise ValueError(message)


def _put_optional(target: dict[str, JsonValue], key: str, value: JsonValue) -> None:
    if value is not None:
        target[key] = value
