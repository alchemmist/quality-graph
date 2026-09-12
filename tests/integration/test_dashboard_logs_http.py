from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest

from qg_github.dashboard import DashboardNode, DashboardRun
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_jobs, publish_workflow_run, watch_workflow_run
from quality_graph_core.result import JsonValue, Result, ResultStatus
from tests.integration.test_github_lifecycle_http import archive, state, workflow_event

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration

RUN_URL = "https://github.com/owner/repository/actions/runs/10"
FORMAT_URL = f"{RUN_URL}/job/101"
LINT_URL = f"{RUN_URL}/job/102"


def scenario() -> dict[str, JsonValue]:
    fixture = state()
    return {
        "contents": {f"{'d' * 40}:qg.yaml": fixture.graph},
        "run_artifacts": {"10": fixture.artifacts},
        "downloads": {
            str(identifier): base64.b64encode(content).decode()
            for identifier, content in fixture.downloads.items()
        },
        "workflow_jobs": {
            "10": [
                {
                    "id": 101,
                    "run_id": 10,
                    "run_attempt": 1,
                    "name": "Formatting",
                    "html_url": FORMAT_URL,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 102,
                    "run_id": 10,
                    "run_attempt": 1,
                    "name": "Lint",
                    "html_url": LINT_URL,
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
        },
    }


def dashboard_body(fake_github: FakeGitHubScenario) -> str:
    comments = cast("list[dict[str, JsonValue]]", fake_github.snapshot()["comments"])
    return cast("str", comments[0]["body"])


def test_final_dashboard_links_each_node_to_its_github_job(
    fake_github: FakeGitHubScenario,
) -> None:
    fake_github.reset(scenario())
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_run(port, workflow_event())

    body = dashboard_body(fake_github)
    format_row = next(line for line in body.splitlines() if line.startswith("| Formatting |"))
    lint_row = next(line for line in body.splitlines() if line.startswith("| Lint |"))
    assert f"[Logs]({FORMAT_URL})" in format_row
    assert f"[Logs]({LINT_URL})" in lint_row


def test_live_links_survive_finalization(fake_github: FakeGitHubScenario) -> None:
    fixture = scenario()
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    fixture["workflow_job_snapshots"] = {
        "10": [[jobs[0] | {"status": "in_progress"}, jobs[1]], jobs]
    }
    fake_github.reset(fixture)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    live_bodies: list[str] = []

    watch_workflow_run(
        port,
        workflow_event() | {"action": "requested"},
        sleep=lambda _: live_bodies.append(dashboard_body(fake_github)),
    )
    publish_workflow_run(port, workflow_event())

    assert len(live_bodies) == 1
    for body in [*live_bodies, dashboard_body(fake_github)]:
        assert f"[Logs]({FORMAT_URL})" in body
        assert f"[Logs]({LINT_URL})" in body


def test_partial_rerun_keeps_the_job_attempt_that_produced_each_result(
    fake_github: FakeGitHubScenario,
) -> None:
    fixture = scenario()
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    jobs.extend(
        [
            jobs[1] | {"id": 202, "run_attempt": 2, "html_url": f"{RUN_URL}/job/202"},
            jobs[0] | {"id": 301, "run_attempt": 3, "html_url": f"{RUN_URL}/job/301"},
            jobs[1] | {"id": 302, "run_attempt": 3, "html_url": f"{RUN_URL}/job/302"},
        ]
    )
    fixture["workflow_attempt_jobs"] = {
        "10": {
            str(attempt): [job for job in jobs if job["run_attempt"] == attempt]
            for attempt in (1, 2, 3)
        }
    }
    original = Result.from_json(
        zipfile.ZipFile(io.BytesIO(state().downloads[2])).read("lint.json").decode()
    )
    content = archive(replace(original, provenance=replace(original.provenance, run_attempt=2)))
    artifacts = cast("dict[str, list[dict[str, JsonValue]]]", fixture["run_artifacts"])["10"]
    artifacts.append(
        {
            "id": 3,
            "name": "quality-result-lint-2",
            "size_in_bytes": len(content),
            "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
            "expired": False,
        }
    )
    cast("dict[str, JsonValue]", fixture["downloads"])["3"] = base64.b64encode(content).decode()
    fake_github.reset(fixture)
    event = workflow_event()
    cast("dict[str, JsonValue]", event["workflow_run"])["run_attempt"] = 2
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_run(port, event)

    body = dashboard_body(fake_github)
    assert f"[Logs]({FORMAT_URL})" in body
    assert f"[Logs]({RUN_URL}/job/202)" in body
    assert "/job/301" not in body
    assert "/job/302" not in body
    assert f"[Logs]({LINT_URL})" not in body


@pytest.mark.parametrize(
    "unresolved", ["missing", "duplicate", "attempt", "identity", "url", "unsafe-url"]
)
def test_unresolved_job_is_explicitly_labeled_as_a_workflow_run(
    fake_github: FakeGitHubScenario,
    unresolved: str,
) -> None:
    fixture = scenario()
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    if unresolved == "missing":
        jobs.pop(0)
    elif unresolved == "duplicate":
        jobs.append(jobs[0] | {"id": 999, "html_url": f"{RUN_URL}/job/999"})
    elif unresolved == "attempt":
        jobs[0]["run_attempt"] = 2
    elif unresolved == "identity":
        jobs[0]["run_id"] = 99
    elif unresolved == "unsafe-url":
        jobs[0]["html_url"] = "javascript:alert(1)"
    else:
        jobs[0]["html_url"] = None
    fake_github.reset(fixture)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_run(port, workflow_event())

    body = dashboard_body(fake_github)
    row = next(line for line in body.splitlines() if line.startswith("| Formatting |"))
    assert "[Workflow run](https://example.test/run/10)" in row
    assert "[Logs]" not in row
    assert f"[Logs]({LINT_URL})" in body


def test_duplicate_graph_titles_do_not_select_a_job_by_title(
    fake_github: FakeGitHubScenario,
) -> None:
    fixture = scenario()
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    jobs[1]["name"] = "Formatting"
    fake_github.reset(fixture)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_jobs(
        port,
        42,
        (DashboardNode("format", "Formatting"), DashboardNode("lint", "Formatting")),
        DashboardRun(10, 1, "a" * 40, RUN_URL),
    )

    rows = [
        line
        for line in dashboard_body(fake_github).splitlines()
        if line.startswith("| Formatting |")
    ]
    assert len(rows) == 2
    assert all("[Workflow run]" in row and "[Logs]" not in row for row in rows)


def test_partial_rerun_waits_for_current_attempt_jobs(fake_github: FakeGitHubScenario) -> None:
    fixture = scenario()
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    current = jobs[1] | {
        "id": 202,
        "run_attempt": 2,
        "html_url": f"{RUN_URL}/job/202",
        "status": "in_progress",
    }
    fixture["workflow_job_snapshots"] = {
        "10": [jobs, [*jobs, current], [*jobs, current | {"status": "completed"}]]
    }
    fake_github.reset(fixture)
    event = workflow_event() | {"action": "requested"}
    cast("dict[str, JsonValue]", event["workflow_run"])["run_attempt"] = 2
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    live_bodies: list[str] = []

    watch_workflow_run(port, event, sleep=lambda _: live_bodies.append(dashboard_body(fake_github)))

    assert len(live_bodies) == 2
    assert f"[Logs]({FORMAT_URL})" in live_bodies[1]
    assert f"[Logs]({RUN_URL}/job/202)" in live_bodies[1]


def test_job_links_are_resolved_across_http_pages(fake_github: FakeGitHubScenario) -> None:
    fixture = scenario()
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    jobs[:0] = [{"name": f"unrelated-{index}", "run_attempt": 1} for index in range(100)]
    fake_github.reset(fixture)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_run(port, workflow_event())

    body = dashboard_body(fake_github)
    assert f"[Logs]({FORMAT_URL})" in body
    assert f"[Logs]({LINT_URL})" in body


def test_artifact_failure_keeps_job_specific_logs(fake_github: FakeGitHubScenario) -> None:
    fixture = scenario()
    artifacts = cast("dict[str, list[dict[str, JsonValue]]]", fixture["run_artifacts"])["10"]
    artifacts[0]["expired"] = True
    fake_github.reset(fixture)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    publish_workflow_run(port, workflow_event())

    body = dashboard_body(fake_github)
    assert "could not be assembled" in body
    assert f"[Logs]({FORMAT_URL})" in body
    assert f"[Logs]({LINT_URL})" in body


@pytest.mark.parametrize("status_code", [404, 500])
@pytest.mark.parametrize("lint_passed", [False, True])
@pytest.mark.parametrize("attempt", [1, 2])
def test_final_publication_survives_unavailable_job_metadata(
    fake_github: FakeGitHubScenario,
    status_code: int,
    attempt: int,
    *,
    lint_passed: bool,
) -> None:
    fixture = scenario()
    if lint_passed:
        original = Result.from_json(
            zipfile.ZipFile(io.BytesIO(state().downloads[2])).read("lint.json").decode()
        )
        content = archive(Result("lint", "Lint", ResultStatus.PASSED, original.provenance))
        cast("dict[str, JsonValue]", fixture["downloads"])["2"] = base64.b64encode(content).decode()
        artifacts = cast("dict[str, list[dict[str, JsonValue]]]", fixture["run_artifacts"])["10"]
        artifacts[1].update(
            size_in_bytes=len(content), digest=f"sha256:{hashlib.sha256(content).hexdigest()}"
        )
    jobs = cast("dict[str, list[dict[str, JsonValue]]]", fixture["workflow_jobs"])["10"]
    jobs[1]["conclusion"] = "success" if lint_passed else "failure"
    fixture["workflow_attempt_jobs"] = {"10": {"1": jobs, "2": []}}
    fixture["failures"] = [
        {
            "method": "GET",
            "path": "/repos/owner/repository/actions/runs/10/jobs",
            "status": status_code,
        }
    ]
    fake_github.reset(fixture)
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)

    event = workflow_event()
    cast("dict[str, JsonValue]", event["workflow_run"])["run_attempt"] = attempt

    outcome = publish_workflow_run(port, event)

    expected = ResultStatus.PASSED if lint_passed else ResultStatus.FAILED
    assert outcome.published is True
    assert outcome.status is expected
    body = dashboard_body(fake_github)
    assert "Formatting | ✅ passed" in body
    assert f"Lint | {'✅' if lint_passed else '❌'} {expected.value}" in body
    assert body.count("[Workflow run](https://example.test/run/10)") == 2
    assert "[Logs]" not in body
    checks = cast("list[dict[str, JsonValue]]", fake_github.snapshot()["checks"])
    assert len(checks) == 1
    assert checks[0]["status"] == "completed"
    assert checks[0]["conclusion"] == ("success" if lint_passed else "failure")
