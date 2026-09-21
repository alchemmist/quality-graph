from __future__ import annotations

import shlex
import sys
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_gitlab import runtime
from qg_gitlab.compiler import graph_digest, starter
from qg_gitlab.markers import execution_marker, gate_marker
from quality_graph_core.graph import Graph
from quality_graph_core.result import FailureKind, Result, ResultStatus

if TYPE_CHECKING:
    from pathlib import Path

    from quality_graph_core.result import JsonValue
    from tests.integration.fake_gitlab import FakeGitLabScenario

pytestmark = pytest.mark.integration


def prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    *,
    server: str = "https://gitlab.example.test",
    mr: bool = False,
) -> Graph:
    value = yaml.safe_load(starter("main"))
    value["provider"]["configuration"].update({"server-url": server, "publisher-user-id": 7})
    value["nodes"]["quality"]["run"] = command
    source = yaml.safe_dump(value)
    (tmp_path / "qg.yaml").write_text(source)
    for key, item in {
        "CI_SERVER_URL": server,
        "CI_PROJECT_ID": "1",
        "CI_COMMIT_SHA": "a" * 40,
        "CI_PIPELINE_ID": "200",
        "CI_JOB_ID": "90",
        "CI_JOB_TOKEN": "fake-token",
    }.items():
        monkeypatch.setenv(key, item)
    monkeypatch.delenv("CI_API_V4_URL", raising=False)
    monkeypatch.delenv("CI_MERGE_REQUEST_IID", raising=False)
    if mr:
        monkeypatch.setenv("CI_MERGE_REQUEST_IID", "4")
        monkeypatch.setenv("CI_MERGE_REQUEST_PROJECT_ID", "1")
    return Graph.from_yaml(source)


def reset(fake: FakeGitLabScenario, values: dict[str, JsonValue]) -> None:
    values["merge_requests"] = {
        "1:4": {"state": "opened", "sha": "a" * 40, "head_pipeline": {"id": 200}}
    }
    fake.reset(values)


def test_gitlab_runtime_executes_the_command_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = (
        "from pathlib import Path; p=Path('count'); "
        "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
    )
    prepare(tmp_path, monkeypatch, shlex.join((sys.executable, "-c", code)))
    assert (
        runtime.main(["execute", "--root", str(tmp_path), "--flow", "push", "--node", "quality"])
        == 0
    )
    assert (tmp_path / "count").read_text() == "1"
    result = Result.from_json((tmp_path / ".qg/results/push/quality.json").read_bytes())
    assert result.status is ResultStatus.PASSED
    assert result.schema_version == 1


def test_gitlab_runtime_records_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(tmp_path, monkeypatch, shlex.join((sys.executable, "-c", "raise SystemExit(10)")))
    assert runtime.execute(tmp_path, "push", "quality") == 1
    result = Result.from_json((tmp_path / ".qg/results/push/quality.json").read_bytes())
    assert result.failure_kind is FailureKind.COMMAND


@pytest.mark.parametrize("kind", ["junit", "sarif", "native"])
def test_gitlab_runtime_missing_reports_are_adapter_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    prepare(tmp_path, monkeypatch, "true")
    source = yaml.safe_load((tmp_path / "qg.yaml").read_text())
    source["nodes"]["quality"]["results"] = {kind: "missing.report"}
    (tmp_path / "qg.yaml").write_text(yaml.safe_dump(source))
    assert runtime.execute(tmp_path, "push", "quality") == 1
    result = Result.from_json((tmp_path / ".qg/results/push/quality.json").read_bytes())
    assert result.failure_kind is FailureKind.ADAPTER


def test_gitlab_runtime_collects_junit_without_a_second_test_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(tmp_path, monkeypatch, "false")
    source = yaml.safe_load((tmp_path / "qg.yaml").read_text())
    source["nodes"]["quality"]["results"] = {"junit": "report.xml"}
    (tmp_path / "qg.yaml").write_text(yaml.safe_dump(source))
    (tmp_path / "report.xml").write_text(
        '<testsuite><testcase name="broken"><failure>failure</failure></testcase></testsuite>'
    )
    assert runtime.execute(tmp_path, "push", "quality") == 10
    result = Result.from_json((tmp_path / ".qg/results/push/quality.json").read_bytes())
    assert result.failure_kind is FailureKind.QUALITY


def test_gitlab_runtime_requires_the_current_job_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gitlab: FakeGitLabScenario
) -> None:
    graph = prepare(tmp_path, monkeypatch, "true", server=fake_gitlab.base_url, mr=True)
    digest = graph_digest(graph)
    reset(
        fake_gitlab,
        {
            "notes": {
                "1:4": [
                    {"id": 1, "author": {"id": 7}, "body": execution_marker(1, 200, 90, digest)}
                ]
            }
        },
    )
    assert runtime.execute(tmp_path, "mr", "quality") == 0
    reset(
        fake_gitlab,
        {
            "notes": {
                "1:4": [
                    {"id": 1, "author": {"id": 7}, "body": execution_marker(1, 200, 89, digest)}
                ]
            }
        },
    )
    clock = iter((0, 0, 300))
    monkeypatch.setattr(runtime, "monotonic", lambda: next(clock, 300))
    monkeypatch.setattr(runtime, "sleep", lambda _seconds: None)
    assert runtime.execute(tmp_path, "mr", "quality") == 1


def test_gitlab_gate_checks_the_author_of_the_installed_status_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gitlab: FakeGitLabScenario
) -> None:
    graph = prepare(tmp_path, monkeypatch, "true", server=fake_gitlab.base_url, mr=True)
    marker = gate_marker(1, 200, "mr", graph_digest(graph))
    reset(fake_gitlab, {"notes": {"1:4": [{"id": 1, "author": {"id": 7}, "body": marker}]}})
    assert runtime.main(["gate", "--root", str(tmp_path), "--flow", "mr"]) == 0
    reset(fake_gitlab, {"notes": {"1:4": [{"id": 1, "author": {"id": 8}, "body": marker}]}})
    clock = iter((0, 0, 300))
    monkeypatch.setattr(runtime, "monotonic", lambda: next(clock, 300))
    monkeypatch.setattr(runtime, "sleep", lambda _seconds: None)
    assert runtime.gate(tmp_path, "mr") == 1


def test_gitlab_runtime_rejects_unknown_nodes_and_foreign_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(tmp_path, monkeypatch, "true")
    with pytest.raises(ValueError, match="unknown GitLab execution"):
        runtime.execute(tmp_path, "push", "unknown")
    monkeypatch.setenv("CI_SERVER_URL", "https://another.example.test")
    with pytest.raises(ValueError, match="server does not match"):
        runtime.execute(tmp_path, "push", "quality")


def test_gitlab_runtime_rejects_merged_results_until_explicitly_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(tmp_path, monkeypatch, "true", mr=True)
    monkeypatch.setenv("CI_MERGE_REQUEST_EVENT_TYPE", "merged_result")
    with pytest.raises(ValueError, match="merged-results"):
        runtime.execute(tmp_path, "mr", "quality")


def test_gitlab_runtime_requires_an_available_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare(tmp_path, monkeypatch, "true")
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="available shell"):
        runtime.execute(tmp_path, "push", "quality")


@pytest.mark.parametrize("change", ["closed", "new-head", "new-pipeline"])
def test_superseded_jobs_release_runner_capacity_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gitlab: FakeGitLabScenario, change: str
) -> None:
    prepare(tmp_path, monkeypatch, "true", server=fake_gitlab.base_url, mr=True)
    mr: dict[str, JsonValue] = {"state": "opened", "sha": "a" * 40, "head_pipeline": {"id": 200}}
    if change == "closed":
        mr["state"] = "closed"
    elif change == "new-head":
        mr["sha"] = "b" * 40
    else:
        mr["head_pipeline"] = {"id": 201}
    fake_gitlab.reset({"merge_requests": {"1:4": mr}})
    waits: list[float] = []
    monkeypatch.setattr(runtime, "sleep", waits.append)
    assert runtime.execute(tmp_path, "mr", "quality") == 1
    assert waits == []


def test_new_job_waits_for_mr_pipeline_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_gitlab: FakeGitLabScenario
) -> None:
    graph = prepare(tmp_path, monkeypatch, "true", server=fake_gitlab.base_url, mr=True)
    current: dict[str, JsonValue] = {
        "state": "opened",
        "sha": "a" * 40,
        "head_pipeline": {"id": 200},
    }
    fake_gitlab.reset(
        {
            "merge_requests": {"1:4": current},
            "mr_snapshots": {"1:4": [{**current, "head_pipeline": {"id": 199}}, current]},
            "notes": {
                "1:4": [
                    {
                        "id": 1,
                        "author": {"id": 7},
                        "body": gate_marker(1, 200, "mr", graph_digest(graph)),
                    }
                ]
            },
        }
    )
    waits: list[float] = []
    monkeypatch.setattr(runtime, "sleep", waits.append)
    assert runtime.gate(tmp_path, "mr") == 0
    assert waits == [runtime.GATE_INTERVAL_SECONDS]
