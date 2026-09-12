from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest

from qg_github.commands import handle_command
from qg_github.compiler import compile_graph
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run, watch_workflow_run
from quality_graph_core.graph import Graph
from quality_graph_core.result import (
    FailureKind,
    Finding,
    JsonValue,
    Provenance,
    Result,
    ResultStatus,
    Severity,
)
from tests.integration.test_artifacts_http import archive, metadata
from tests.integration.test_commands_http import event as command_event
from tests.test_graph import GRAPH

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def node_result(attempt: int, status: ResultStatus = ResultStatus.PASSED) -> Result:
    return Result(
        "lint",
        "Lint",
        status,
        Provenance(
            "owner/repository",
            "a" * 40,
            10,
            attempt,
            compile_graph(Graph.from_yaml(GRAPH)).graph_digest,
            42,
        ),
        FailureKind.COMMAND if status is ResultStatus.FAILED else None,
    )


def scenario(
    results: list[Result],
    *,
    conclusion: str = "success",
    lint_attempt: int = 3,
    paginate: bool = False,
) -> dict[str, JsonValue]:
    formatting = replace(node_result(2), node_id="format", title="Formatting")
    artifacts: list[JsonValue] = []
    downloads: dict[str, JsonValue] = {}
    for identifier, result in enumerate([formatting, *results], start=1):
        content = archive(result)
        artifacts.append(
            metadata(
                identifier,
                f"quality-result-{result.node_id}-{result.provenance.run_attempt}",
                content,
            )
        )
        downloads[str(identifier)] = base64.b64encode(content).decode()
    if paginate:
        artifacts[2:2] = [
            metadata(identifier, f"other-{identifier}", b"") for identifier in range(100, 198)
        ]
    run: dict[str, JsonValue] = {
        "id": 10,
        "run_attempt": 3,
        "status": "completed",
        "conclusion": conclusion,
        "event": "pull_request",
        "head_sha": "a" * 40,
        "html_url": "https://example.test/run/10",
        "pull_requests": [{"number": 42, "head": {"sha": "a" * 40}}],
    }
    old_jobs: list[JsonValue] = [
        {
            "id": 20,
            "run_id": 10,
            "run_attempt": 2,
            "name": "Formatting",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 21,
            "run_id": 10,
            "run_attempt": 2,
            "name": "Lint",
            "status": "completed",
            "conclusion": "success",
        },
    ]
    current_jobs: list[JsonValue] = (
        [
            {
                "id": 30,
                "run_id": 10,
                "run_attempt": 3,
                "name": "Lint",
                "status": "completed",
                "conclusion": conclusion,
            },
        ]
        if lint_attempt == 3
        else []
    )
    return {
        "contents": {f"{'d' * 40}:qg.yaml": GRAPH},
        "workflow_runs": [run],
        "workflow_attempt_jobs": {"10": {"2": old_jobs, "3": current_jobs}},
        "run_artifacts": {"10": artifacts},
        "downloads": downloads,
    }


def publish(
    github: FakeGitHubScenario,
    results: list[Result],
    *,
    conclusion: str = "success",
    lint_attempt: int = 3,
    paginate: bool = False,
) -> dict[str, JsonValue]:
    payload = scenario(results, conclusion=conclusion, lint_attempt=lint_attempt, paginate=paginate)
    return publish_scenario(github, payload)


def publish_scenario(
    github: FakeGitHubScenario, payload: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    github.reset(payload)
    run = cast("list[JsonValue]", payload["workflow_runs"])[0]
    outcome = publish_workflow_run(
        HttpGitHubPort("owner/repository", "token", base_url=github.base_url),
        {"action": "completed", "workflow_run": run},
    )
    assert outcome.published
    observed = github.snapshot()
    checks = cast("list[dict[str, JsonValue]]", observed["checks"])
    assert len(checks) == 1
    assert checks[0]["status"] == "completed"
    assert (
        checks[0]["external_id"]
        == f"quality-graph:10:{cast('dict[str, JsonValue]', run)['run_attempt']}"
    )
    requests = cast("list[dict[str, JsonValue]]", observed["requests"])
    assert any(
        request["path"] == "/repos/owner/repository/actions/workflows/quality-graph.yml/runs"
        for request in requests
    )
    return checks[0]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_publisher_rejects_stale_success_after_node_rerun(
    fake_github: FakeGitHubScenario,
    conclusion: str,
) -> None:
    check = publish(fake_github, [node_result(2)], conclusion=conclusion)
    assert check["conclusion"] == "failure"


def test_publisher_rejects_future_attempt(fake_github: FakeGitHubScenario) -> None:
    check = publish(fake_github, [node_result(4)])
    assert check["conclusion"] == "failure"


def test_publisher_rejects_results_straddling_event_attempt(
    fake_github: FakeGitHubScenario,
) -> None:
    check = publish(fake_github, [node_result(2, ResultStatus.FAILED), node_result(4)])
    assert check["conclusion"] == "failure"


@pytest.mark.parametrize("reverse", [False, True], ids=["failed-then-passed", "passed-then-failed"])
@pytest.mark.parametrize("paginate", [False, True], ids=["one-page", "across-pages"])
def test_publisher_rejects_conflicting_same_attempt_artifacts(
    fake_github: FakeGitHubScenario,
    *,
    reverse: bool,
    paginate: bool,
) -> None:
    results = [node_result(3, ResultStatus.FAILED), node_result(3)]
    check = publish(fake_github, list(reversed(results)) if reverse else results, paginate=paginate)
    assert check["conclusion"] == "failure"


@pytest.mark.parametrize("status", [ResultStatus.PASSED, ResultStatus.FAILED])
def test_publisher_accepts_current_result_with_retained_unrerun_node(
    fake_github: FakeGitHubScenario,
    status: ResultStatus,
) -> None:
    check = publish(
        fake_github,
        [node_result(3, status)],
        conclusion="success" if status is ResultStatus.PASSED else "failure",
    )
    assert check["conclusion"] == ("success" if status is ResultStatus.PASSED else "failure")


def test_publisher_preserves_success_when_node_was_not_rerun(
    fake_github: FakeGitHubScenario,
) -> None:
    check = publish(fake_github, [node_result(2)], lint_attempt=2)
    assert check["conclusion"] == "success"


@pytest.mark.parametrize("case", ["missing", "wrong-run", "wrong-head"])
def test_publisher_attempt_validation_controls(fake_github: FakeGitHubScenario, case: str) -> None:
    result = node_result(3)
    if case == "wrong-run":
        result = replace(result, provenance=replace(result.provenance, workflow_run_id=9))
    if case == "wrong-head":
        result = replace(result, provenance=replace(result.provenance, head_sha="b" * 40))
    check = publish(fake_github, [] if case == "missing" else [result])
    assert check["conclusion"] == "failure"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_current_pass_cannot_override_failed_job(
    fake_github: FakeGitHubScenario,
    conclusion: str,
) -> None:
    assert publish(fake_github, [node_result(3)], conclusion=conclusion)["conclusion"] == "failure"


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "unknown-attempt",
        "duplicate",
        "wrong-run",
        "wrong-attempt",
        "in-progress",
        "missing-conclusion",
        "invalid-attempt",
    ],
)
def test_publisher_rejects_unusable_job_evidence(
    fake_github: FakeGitHubScenario,
    case: str,
) -> None:
    payload = scenario([node_result(3)])
    history = cast(
        "dict[str, dict[str, list[dict[str, JsonValue]]]]", payload["workflow_attempt_jobs"]
    )["10"]
    current = history["3"][0]
    if case == "missing":
        history.update({"1": [], "2": [], "3": []})
    elif case == "unknown-attempt":
        del history["3"]
    elif case == "duplicate":
        history["3"].append(current | {"id": 31})
    elif case == "invalid-attempt":
        cast("list[dict[str, JsonValue]]", payload["workflow_runs"])[0]["run_attempt"] = 0
    else:
        field, value = {
            "wrong-run": ("run_id", 11),
            "wrong-attempt": ("run_attempt", 4),
            "in-progress": ("status", "in_progress"),
            "missing-conclusion": ("conclusion", None),
        }[case]
        current[field] = value
    assert publish_scenario(fake_github, payload)["conclusion"] == "failure"


def test_publisher_resolves_job_evidence_across_pages(fake_github: FakeGitHubScenario) -> None:
    payload = scenario([node_result(3)])
    history = cast(
        "dict[str, dict[str, list[dict[str, JsonValue]]]]", payload["workflow_attempt_jobs"]
    )["10"]
    history["3"][0:0] = [{"name": f"Other {number}"} for number in range(100)]
    assert publish_scenario(fake_github, payload)["conclusion"] == "success"


@pytest.mark.parametrize("attempt", [2, 3])
def test_watcher_uses_same_attempt_admission(fake_github: FakeGitHubScenario, attempt: int) -> None:
    payload = scenario([node_result(attempt)])
    fake_github.reset(payload)
    run = cast("list[JsonValue]", payload["workflow_runs"])[0]
    outcome = watch_workflow_run(
        HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url),
        {"action": "requested", "workflow_run": run},
        sleep=lambda _: None,
    )
    assert outcome.status is (ResultStatus.PASSED if attempt == 3 else ResultStatus.FAILED)
    checks = cast("list[dict[str, JsonValue]]", fake_github.snapshot()["checks"])
    assert checks[0]["conclusion"] == ("success" if attempt == 3 else "failure")


def test_identical_duplicate_results_are_order_independent(fake_github: FakeGitHubScenario) -> None:
    result = node_result(3)
    assert publish(fake_github, [result, result], paginate=True)["conclusion"] == "success"


@pytest.mark.parametrize("attempt", [2, 3])
def test_commands_only_approve_findings_from_admitted_attempt(
    fake_github: FakeGitHubScenario,
    attempt: int,
) -> None:
    result = replace(
        node_result(attempt, ResultStatus.FAILED),
        failure_kind=FailureKind.QUALITY,
        findings=(Finding("finding", Severity.ERROR, "Failure"),),
    )
    payload = scenario([result], conclusion="failure")
    payload["permissions"] = {"admin": "admin"}
    fake_github.reset(payload)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    if attempt == 2:
        with pytest.raises(ValueError, match="unknown or non-approvable"):
            handle_command(port, command_event("/qg ignore finding"))
    else:
        assert handle_command(port, command_event("/qg ignore finding")).changed
    observed = fake_github.snapshot()
    assert observed["reruns"] == ([10] if attempt == 3 else [])
    approvals = [
        comment
        for comment in cast("list[dict[str, JsonValue]]", observed["comments"])
        if "quality-graph:approval" in str(comment["body"])
    ]
    assert len(approvals) == (1 if attempt == 3 else 0)
    if attempt == 3:
        run = cast("list[JsonValue]", payload["workflow_runs"])[0]
        publication = publish_workflow_run(port, {"action": "completed", "workflow_run": run})
        assert publication.status is ResultStatus.PASSED
