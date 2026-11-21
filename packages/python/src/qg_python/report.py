"""Render deterministic source diagnostics for local and CI gates."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_PATH = 4096

type JsonValue = str | int | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True, order=True)
class Finding:
    """Describe one source-located quality violation."""

    path: str
    line: int
    column: int
    message: str

    def diagnostic(self) -> str:
        """Render the shared compiler-style diagnostic format."""
        return f"{self.path}:{self.line}:{self.column}: error: {self.message}"


def report(findings: tuple[Finding, ...]) -> int:
    """Print sorted findings and return the gate exit code."""
    if findings:
        sys.stdout.write("\n".join(finding.diagnostic() for finding in sorted(findings)) + "\n")
    return int(bool(findings))


def run_gate[T](
    rule: str,
    output: Path | None,
    select: Callable[[], tuple[T, ...]],
    scan: Callable[[T], tuple[Finding, ...]],
) -> int:
    """Scan once and render the same diagnostics locally and to the native adapter."""
    payload: dict[str, JsonValue] = {}
    try:
        files = select()
        findings = tuple(sorted({item for file in files for item in scan(file)}))
        if output is not None:
            payload = native_report(rule, findings, len(files))
        code = report(findings)
    except (OSError, ValueError, SyntaxError, RuntimeError, CalledProcessError) as error:
        message = f"Cannot scan sources: {error}"[:1000]
        sys.stderr.write(message + "\n")
        payload = {
            "reportVersion": 0,
            "status": "failed",
            "failureKind": "infrastructure",
            "summary": message,
            "diagnostics": [{"kind": "infrastructure", "message": message, "detail": ""}],
        }
        code = 2
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return code


def native_report(rule: str, findings: tuple[Finding, ...], scanned: int) -> dict[str, JsonValue]:
    """Build a producer-v0 report; the collector supplies trusted identity and provenance."""
    items: list[JsonValue] = []
    annotations: list[JsonValue] = []
    occurrences: Counter[tuple[str, str]] = Counter()
    for item in findings[:10_000]:
        path = Path(item.path).absolute().relative_to(Path.cwd()).as_posix()
        if ".." in Path(path).parts or "\\" in path or len(path) > MAX_PATH:
            message = f"Unsupported source path: {path}"
            raise ValueError(message)
        key = (path, item.message)
        occurrences[key] += 1
        identity = json.dumps([rule, *key, occurrences[key]])
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        location: dict[str, JsonValue] = {
            "path": path,
            "startLine": item.line,
            "endLine": item.line,
            "startColumn": item.column,
            "endColumn": item.column,
        }
        shared: dict[str, JsonValue] = {"message": item.message[:1000], "location": location}
        items.append(
            shared
            | {"severity": "error", "ruleId": rule, "fingerprint": fingerprint, "id": fingerprint}
        )
        annotations.append(shared | {"level": "error"})
    result: dict[str, JsonValue] = {
        "reportVersion": 0,
        "status": "failed" if findings else "passed",
        "summary": f"Scanned {scanned} files; found {len(findings)} violations.",
        "metrics": [
            {"label": "Files scanned", "value": str(scanned)},
            {"label": "Findings found", "value": str(len(findings))},
        ],
        "findings": items,
        "annotations": annotations,
        "omittedFindings": len(findings) - len(items),
    }
    if findings:
        result["failureKind"] = "quality"
    return result
