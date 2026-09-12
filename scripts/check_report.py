"""Run repository checks and preserve producer data for central reporting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, cast

from quality_graph_core.adapters import AdapterError, junit_report, read_report

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue

COMMAND_NOT_FOUND = 127


def execute(
    command: list[str], group: str, junit: Path | None = None, *, infrastructure: bool = False
) -> tuple[int, dict[str, JsonValue]]:
    """Execute an explicit argv and collect bounded domain diagnostics."""
    if junit is not None:
        junit.parent.mkdir(parents=True, exist_ok=True)
        junit.unlink(missing_ok=True)
    code, detail = _command_output(command)
    command_code = code
    report: dict[str, JsonValue] = {"reportVersion": 0, "status": "passed", "diagnostics": []}
    if junit is not None:
        try:
            report = junit_report(read_report(Path.cwd(), str(junit)))
        except (AdapterError, OSError, ValueError, TypeError) as error:
            code = code or 2
            report.update(
                status="failed",
                failureKind="adapter",
                diagnostics=[
                    {
                        "kind": "adapter",
                        "message": str(error)[:1_000],
                        "detail": detail,
                    }
                ],
            )
    if code and report["status"] != "failed":
        report.update(
            status="failed",
            failureKind="infrastructure"
            if junit is not None or code == COMMAND_NOT_FOUND
            else "command",
        )
    if code and not report.get("diagnostics"):
        report["diagnostics"] = [
            {
                "kind": "infrastructure"
                if infrastructure or code == COMMAND_NOT_FOUND
                else "command",
                "message": f"Command exited with status {code}",
                "detail": detail,
            }
        ]
    if command_code and (infrastructure or (junit is not None and command_code > 1)):
        report.update(status="failed", failureKind="infrastructure")
    if report["status"] == "failed":
        code = code or 1
    notes = cast("list[JsonValue]", report.get("notes", []))
    report["notes"] = [*notes, "failed" if code else "passed"]
    return code, report


def _command_output(command: list[str]) -> tuple[int, str]:
    tail: deque[str] = deque(maxlen=5)
    try:
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
        ) as process:
            if process.stdout is None:
                message = "command output pipe is unavailable"
                raise RuntimeError(message)
            while chunk := process.stdout.read(4096):
                sys.stdout.write(chunk)
                tail.append(chunk)
            code = process.wait()
    except OSError as error:
        code = COMMAND_NOT_FOUND
        tail.append(str(error))
    detail = "".join(tail)[-20_000:]
    return code, detail


def combine(reports: list[tuple[str, dict[str, JsonValue]]]) -> dict[str, JsonValue]:
    """Combine named executions without losing finding identity or adapter context."""
    output: dict[str, JsonValue] = {"reportVersion": 0, "status": "passed"}
    failures = [report for _, report in reports if report["status"] == "failed"]
    if failures:
        output.update(
            status="failed",
            failureKind=next(
                (
                    report["failureKind"]
                    for report in failures
                    if report["failureKind"] != "quality"
                ),
                "quality",
            ),
        )
    for field in ("metrics", "findings", "diagnostics", "notes"):
        values: list[JsonValue] = []
        for group, report in reports:
            for original in cast("list[JsonValue]", report.get(field, [])):
                item = f"{group}: {original}" if field == "notes" else original
                if isinstance(original, dict):
                    item = dict(original)
                    if field == "metrics":
                        item["label"] = f"{group} / {item['label']}"[:100]
                    elif field == "findings":
                        item["group"] = group
                        item["id"] = (
                            f"{hashlib.sha256(group.encode()).hexdigest()[:8]}:{item['id']}"
                        )
                    else:
                        item["message"] = f"{group}: {item['message']}"[:1_000]
                values.append(item)
        limit = 10_000 if field == "findings" else 100
        output[field] = values[:limit]
    return output


def main() -> int:
    """Run the repository test suites or one declared check command."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("fast", "medium"))
    parser.add_argument("--compose", default="docker compose")
    parser.add_argument("--group", default="Check")
    parser.add_argument("--junit", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.unlink(missing_ok=True)
    reports: list[tuple[str, dict[str, JsonValue]]] = []
    codes: list[int] = []

    def run(
        command: list[str], group: str, junit: Path | None = None, *, infrastructure: bool = False
    ) -> int:
        code, report = execute(command, group, junit, infrastructure=infrastructure)
        reports.append((group, report))
        codes.append(code)
        return code

    if args.suite:
        junit = args.output.with_suffix(".in-process.xml")
        selection = "not integration" if args.suite == "fast" else "integration"
        command = [sys.executable, "-m", "pytest", "-q", "-m", selection]
        run([*command, f"--junitxml={junit}"], "in-process", junit)
        if args.suite == "medium":
            compose = [*shlex.split(args.compose), "-f", "tests/integration/docker-compose.yml"]
            try:
                if (
                    run(
                        [*compose, "up", "-d", "--build", "--wait"],
                        "Docker setup",
                        infrastructure=True,
                    )
                    == 0
                ):
                    junit = args.output.with_suffix(".docker.xml")
                    os.environ["QG_FAKE_GITHUB_URL"] = (
                        f"http://127.0.0.1:{os.environ.get('QG_FAKE_GITHUB_PORT', '18080')}"
                    )
                    run([*command, f"--junitxml={junit}"], "docker", junit)
            finally:
                run([*compose, "down"], "Docker cleanup", infrastructure=True)
    else:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("a command or --suite is required")
        run(command, args.group, args.junit)
    args.output.write_text(json.dumps(combine(reports), indent=2, sort_keys=True) + "\n")
    return int(any(codes))


if __name__ == "__main__":
    raise SystemExit(main())
