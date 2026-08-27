"""Execute the pinned GitHub Action runtime behind generated workflows."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from qg_github.annotations import escape_data, publish_annotations
from qg_github.commands import handle_command
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run, read_event_json
from qg_github.reporting import append_job_summary
from quality_graph_core.adapters import (
    AdapterContext,
    AdapterError,
    adapt_exit,
    adapt_junit,
    adapt_native,
    adapt_sarif,
    adapter_failure,
    read_report,
)
from quality_graph_core.graph import AdapterKind, ApprovalPolicy
from quality_graph_core.policy import policy_controls
from quality_graph_core.result import FailureKind, JsonValue, Provenance, Result, ResultStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True)
class CollectionRequest:
    """Carry trusted workflow context and declared adapter inputs."""

    context: AdapterContext
    adapter: AdapterKind
    report_path: str | None
    workspace: Path
    result_path: Path
    summary_path: Path | None
    output_path: Path
    approval_policy: ApprovalPolicy

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str],
        event: Mapping[str, JsonValue],
    ) -> CollectionRequest:
        """Build a request from explicit GitHub Action environment values."""
        adapter = AdapterKind(_required(environment, "QG_ADAPTER"))
        report = environment.get("QG_REPORT_PATH") or None
        workspace = Path(_required(environment, "GITHUB_WORKSPACE"))
        node_id = _required(environment, "QG_NODE_ID")
        result_path = (
            Path(_required(environment, "RUNNER_TEMP")) / "quality-graph" / f"{node_id}.json"
        )
        summary = environment.get("GITHUB_STEP_SUMMARY")
        provenance = _provenance(environment, event)
        context = AdapterContext(
            node_id,
            _required(environment, "QG_TITLE"),
            environment.get("QG_COMMAND_OUTCOME", "success") == "success",
            provenance,
        )
        return cls(
            context,
            adapter,
            report,
            workspace,
            result_path,
            Path(summary) if summary else None,
            Path(_required(environment, "GITHUB_OUTPUT")),
            ApprovalPolicy(
                _boolean(environment, "QG_APPROVAL_FINDINGS"),
                _boolean(environment, "QG_APPROVAL_FILES"),
                _boolean(environment, "QG_APPROVAL_NODE"),
            ),
        )


def collect(request: CollectionRequest) -> Result:
    """Collect one command outcome through its selected adapter."""
    try:
        if request.adapter is AdapterKind.EXIT_CODE:
            state = "passed" if request.context.command_succeeded else "failed"
            return _with_policy_controls(
                request,
                adapt_exit(request.context, f"The declared command {state}."),
            )
        report = _structured_report(request)
        if request.adapter is AdapterKind.NATIVE:
            return _with_policy_controls(request, adapt_native(request.context, report))
        if request.adapter is AdapterKind.SARIF:
            result = adapt_sarif(request.context, report)
        else:
            result = adapt_junit(request.context, report)
        return _with_policy_controls(request, result)
    except AdapterError as error:
        return _with_policy_controls(request, adapter_failure(request.context, error))


def _with_policy_controls(request: CollectionRequest, result: Result) -> Result:
    controls = policy_controls(result.node_id, result.findings, request.approval_policy)
    return replace(result, controls=controls)


def _structured_report(request: CollectionRequest) -> bytes:
    if request.report_path is None:
        message = f"{request.adapter.value} adapter requires a report path"
        raise AdapterError(message)
    return read_report(request.workspace, request.report_path)


def publish_collection(request: CollectionRequest, result: Result) -> int:
    """Write result, summary, annotations, and Action outputs."""
    request.result_path.parent.mkdir(parents=True, exist_ok=True)
    request.result_path.write_text(result.to_json())
    if request.summary_path is not None:
        append_job_summary(request.summary_path, result)
    publish_annotations(result.annotations)
    exit_code = _result_exit_code(result)
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    with request.output_path.open("a") as output:
        output.write(f"result-path={request.result_path}\n")
        output.write(f"exit-code={exit_code}\n")
    return exit_code


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one generated-workflow runtime operation."""
    operation = list(arguments if arguments is not None else sys.argv[1:])
    if operation == ["collect"]:
        event = _read_event(Path(os.environ["GITHUB_EVENT_PATH"]))
        request = CollectionRequest.from_environment(os.environ, event)
        publish_collection(request, collect(request))
        return 0
    if operation == ["publish"]:
        event = read_event_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        publish_workflow_run(HttpGitHubPort.from_environment(), event)
        return 0
    if operation == ["command"]:
        event = read_event_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        handle_command(HttpGitHubPort.from_environment(), event)
        return 0
    message = f"Unsupported Quality Graph runtime operation: {' '.join(operation)}"
    raise SystemExit(message)


def entrypoint(arguments: Sequence[str] | None = None) -> int:
    """Expose runtime failures as safe public workflow annotations."""
    try:
        return main(arguments)
    except Exception as error:
        name = type(error).__name__
        detail = escape_data(str(error))
        sys.stderr.write(f"::error title=Quality Graph runtime::{name}: {detail}\n")
        raise


def _provenance(
    environment: Mapping[str, str],
    event: Mapping[str, JsonValue],
) -> Provenance:
    pull = event.get("pull_request")
    pull_number: int | None = None
    head_sha = _required(environment, "GITHUB_SHA")
    if isinstance(pull, dict):
        number = pull.get("number")
        head = pull.get("head")
        if isinstance(number, int) and not isinstance(number, bool):
            pull_number = number
        if isinstance(head, dict) and isinstance(head.get("sha"), str):
            head_sha = cast("str", head["sha"])
    return Provenance(
        _required(environment, "GITHUB_REPOSITORY"),
        head_sha,
        int(_required(environment, "GITHUB_RUN_ID")),
        int(_required(environment, "GITHUB_RUN_ATTEMPT")),
        _required(environment, "QG_GRAPH_DIGEST"),
        pull_number,
    )


def _read_event(path: Path) -> dict[str, JsonValue]:
    value = cast("JsonValue", json.loads(path.read_text()))
    if not isinstance(value, dict):
        message = "GitHub event payload must be an object"
        raise TypeError(message)
    return value


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if not value:
        message = f"Required environment variable is missing: {name}"
        raise ValueError(message)
    return value


def _boolean(environment: Mapping[str, str], name: str) -> bool:
    value = _required(environment, name)
    if value not in {"true", "false"}:
        message = f"Environment variable must be true or false: {name}"
        raise ValueError(message)
    return value == "true"


def _result_exit_code(result: Result) -> int:
    if result.status not in {ResultStatus.FAILED, ResultStatus.CANCELLED}:
        return 0
    return 1 if result.failure_kind in {FailureKind.QUALITY, FailureKind.COMMAND} else 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
