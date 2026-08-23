import json
import runpy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from quality_graph.graph import AdapterKind
from quality_graph.result import FailureKind, ResultStatus
from quality_graph.runtime import CollectionRequest, collect, main, publish_collection


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
        main(["publish"])
    values = environment(tmp_path)
    Path(values["GITHUB_EVENT_PATH"]).write_text("[]")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(TypeError, match="payload must be an object"):
        main(["collect"])


def test_collection_request_requires_explicit_environment(tmp_path: Path) -> None:
    values = environment(tmp_path)
    values.pop("QG_NODE_ID")
    with pytest.raises(ValueError, match="QG_NODE_ID"):
        CollectionRequest.from_environment(values, {})


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
    monkeypatch.setattr(sys, "argv", ["quality_graph.runtime", "collect"])

    with pytest.warns(RuntimeWarning), pytest.raises(SystemExit, match="0"):
        runpy.run_module("quality_graph.runtime", run_name="__main__")
