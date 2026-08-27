import json
import runpy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from qg_github.github import MemoryGitHubPort
from qg_github.publication import PublicationOutcome
from qg_github.runtime import (
    CollectionRequest,
    collect,
    entrypoint,
    main,
    publish_collection,
)
from quality_graph_core.graph import AdapterKind
from quality_graph_core.result import FailureKind, ResultStatus


def environment(tmp_path: Path, *, outcome: str = "success") -> dict[str, str]:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42, "head": {"sha": "c" * 40}}}))
    return {
        "QG_NODE_ID": "lint",
        "QG_TITLE": "Lint",
        "QG_ADAPTER": "exit-code",
        "QG_REPORT_PATH": "",
        "QG_COMMAND_OUTCOME": outcome,
        "QG_GRAPH_DIGEST": "b" * 64,
        "QG_APPROVAL_FINDINGS": "false",
        "QG_APPROVAL_FILES": "false",
        "QG_APPROVAL_NODE": "false",
        "GITHUB_WORKSPACE": str(tmp_path),
        "GITHUB_REPOSITORY": "owner/repository",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "100",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_EVENT_PATH": str(event),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "RUNNER_TEMP": str(tmp_path / "runner"),
    }


def event() -> dict[str, object]:
    return {"pull_request": {"number": 42, "head": {"sha": "c" * 40}}}


def test_collection_request_uses_pull_request_head_provenance(tmp_path: Path) -> None:
    request = CollectionRequest.from_environment(environment(tmp_path), event())

    assert request.context.provenance.pull_request == 42
    assert request.context.provenance.head_sha == "c" * 40
    assert request.result_path.name == "lint.json"


def test_collector_publishes_exit_result_summary_and_outputs(tmp_path: Path) -> None:
    request = CollectionRequest.from_environment(environment(tmp_path, outcome="failure"), event())
    result = collect(request)

    assert result.failure_kind is FailureKind.COMMAND
    assert publish_collection(request, result) == 1
    assert request.result_path.is_file()
    assert "## ❌ Lint" in request.summary_path.read_text()
    assert "exit-code=1" in request.output_path.read_text()


def test_collector_uses_structured_adapter_and_reports_missing_input(tmp_path: Path) -> None:
    request = CollectionRequest.from_environment(environment(tmp_path), event())
    missing = replace(request, adapter=AdapterKind.SARIF, report_path=None)

    result = collect(missing)

    assert result.failure_kind is FailureKind.ADAPTER
    assert publish_collection(replace(missing, summary_path=None), result) == 2


def test_collector_reads_sarif_junit_and_native_reports(tmp_path: Path) -> None:
    request = CollectionRequest.from_environment(environment(tmp_path), event())
    sarif = tmp_path / "result.sarif"
    sarif.write_text('{"runs":[{"results":[]}]}')
    junit = tmp_path / "result.xml"
    junit.write_text('<testsuite><testcase name="passes" /></testsuite>')

    sarif_result = collect(replace(request, adapter=AdapterKind.SARIF, report_path="result.sarif"))
    junit_result = collect(replace(request, adapter=AdapterKind.JUNIT, report_path="result.xml"))
    assert sarif_result.status is ResultStatus.PASSED
    assert junit_result.status is ResultStatus.PASSED

    native_result = collect(request)
    native = tmp_path / "result.json"
    native.write_text(native_result.to_json())
    collected_native = collect(
        replace(request, adapter=AdapterKind.NATIVE, report_path="result.json")
    )
    assert collected_native == native_result


def test_collector_replaces_native_controls_with_graph_policy_controls(tmp_path: Path) -> None:
    values = environment(tmp_path)
    values["QG_APPROVAL_NODE"] = "true"
    request = CollectionRequest.from_environment(values, event())
    native_result = collect(request)
    native = tmp_path / "result.json"
    native.write_text(native_result.to_json().replace('"target": "lint"', '"target": "forged"'))

    collected = collect(replace(request, adapter=AdapterKind.NATIVE, report_path="result.json"))

    assert tuple(control.target for control in collected.controls) == ("lint",)


def test_runtime_main_executes_collect_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in environment(tmp_path).items():
        monkeypatch.setenv(name, value)

    assert main(["collect"]) == 0
    assert (tmp_path / "runner" / "quality-graph" / "lint.json").is_file()


def test_runtime_rejects_unknown_operation_and_event_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SystemExit, match="Unsupported"):
        main(["unknown"])
    values = environment(tmp_path)
    Path(values["GITHUB_EVENT_PATH"]).write_text("[]")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(TypeError, match="payload must be an object"):
        main(["collect"])


def test_runtime_entrypoint_publishes_safe_error_annotation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(KeyError):
        entrypoint(["publish"])

    error = capsys.readouterr().err
    assert error.startswith("::error title=Quality Graph runtime::KeyError:")
    assert "\n" not in error.rstrip("\n")


def test_runtime_main_dispatches_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = environment(tmp_path)
    Path(values["GITHUB_EVENT_PATH"]).write_text("{}")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    port = MemoryGitHubPort()
    observed: list[object] = []

    def from_environment() -> MemoryGitHubPort:
        return port

    def publish(selected: object, event_value: object) -> PublicationOutcome:
        observed.extend((selected, event_value))
        return PublicationOutcome(published=False)

    monkeypatch.setattr("qg_github.runtime.HttpGitHubPort.from_environment", from_environment)
    monkeypatch.setattr("qg_github.runtime.publish_workflow_run", publish)

    assert main(["publish"]) == 0
    assert observed == [port, {}]

    observed.clear()
    monkeypatch.setattr("qg_github.runtime.watch_workflow_run", publish)
    assert main(["watch"]) == 0
    assert observed == [port, {}]

    observed.clear()
    monkeypatch.setattr("qg_github.runtime.handle_command", publish)
    assert main(["command"]) == 0
    assert observed == [port, {}]


def test_collection_request_requires_explicit_environment(tmp_path: Path) -> None:
    values = environment(tmp_path)
    values.pop("QG_NODE_ID")
    with pytest.raises(ValueError, match="QG_NODE_ID"):
        CollectionRequest.from_environment(values, {})

    invalid = environment(tmp_path)
    invalid["QG_APPROVAL_NODE"] = "yes"
    with pytest.raises(ValueError, match="must be true or false"):
        CollectionRequest.from_environment(invalid, {})


def test_collection_request_uses_push_sha_without_valid_pull_metadata(tmp_path: Path) -> None:
    values = environment(tmp_path)

    push = CollectionRequest.from_environment(values, {})
    malformed_pull = CollectionRequest.from_environment(
        values,
        {"pull_request": {"number": "42", "head": {"sha": 1}}},
    )

    assert push.context.provenance.pull_request is None
    assert push.context.provenance.head_sha == "a" * 40
    assert malformed_pull.context.provenance.pull_request is None
    assert malformed_pull.context.provenance.head_sha == "a" * 40


def test_runtime_module_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in environment(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["qg_github.runtime", "collect"])

    with pytest.warns(RuntimeWarning), pytest.raises(SystemExit, match="0"):
        runpy.run_module("qg_github.runtime", run_name="__main__")
