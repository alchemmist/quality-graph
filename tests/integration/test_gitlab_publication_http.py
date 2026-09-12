from __future__ import annotations

import base64
import io
import zipfile
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_gitlab.api import HttpGitLab
from qg_gitlab.compiler import graph_digest, starter
from qg_gitlab.publication import publish_merge_request
from quality_graph_core.graph import Graph
from quality_graph_core.result import FailureKind, GitLabProvenance, Result, ResultStatus

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue
    from tests.integration.fake_gitlab import FakeGitLabScenario

pytestmark = pytest.mark.integration
HEAD = "a" * 40
BASE = "b" * 40
RESULT_PATH = ".qg/results/mr/quality.json"


def graph_for(fake: FakeGitLabScenario) -> tuple[Graph, str]:
    value = yaml.safe_load(starter("main"))
    value["provider"]["configuration"].update({"server-url": fake.base_url, "publisher-user-id": 7})
    source = yaml.safe_dump(value)
    return Graph.from_yaml(source), source


def result_for(fake: FakeGitLabScenario, job: int = 90) -> Result:
    graph, _source = graph_for(fake)
    provenance = GitLabProvenance(
        fake.base_url, 1, HEAD, 200, job, graph_digest(graph), 1, 4, "mr", "quality"
    )
    return Result("quality", "Quality", ResultStatus.PASSED, provenance)


def archive(results: list[Result]) -> str:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as output:
        for index, result in enumerate(results):
            if index:
                with pytest.warns(UserWarning, match="Duplicate name"):
                    output.writestr(RESULT_PATH, result.to_json())
            else:
                output.writestr(RESULT_PATH, result.to_json())
    return base64.b64encode(payload.getvalue()).decode()


def scenario(
    fake: FakeGitLabScenario, results: list[Result], status: str = "success"
) -> dict[str, JsonValue]:
    _graph, source = graph_for(fake)
    return {
        "actor": 7,
        "jobs": {
            "1:200": [
                {
                    "id": 90,
                    "name": "qg:mr:quality",
                    "status": status,
                    "allow_failure": False,
                    "pipeline": {"id": 200, "project_id": 1, "sha": HEAD},
                    "web_url": f"{fake.base_url}/jobs/90",
                }
            ]
        },
        "artifacts": {"1:90": archive(results)} if results else {},
        "merge_requests": {
            "1:4": {
                "id": 4,
                "iid": 4,
                "state": "opened",
                "sha": HEAD,
                "head_pipeline": {"id": 200, "project_id": 1},
                "target_branch": "main",
            }
        },
        "resources": {
            "/projects/1/repository/branches/main": {"commit": {"id": BASE}},
            "/projects/1/repository/files/qg.yaml/raw": source,
        },
    }


def publish(fake: FakeGitLabScenario) -> str:
    with HttpGitLab(fake.base_url, "publisher-token") as api:
        outcome = publish_merge_request(api, 1, 4)
    assert outcome is not None
    return outcome.state


def test_gitlab_publishes_one_idempotent_owned_summary(fake_gitlab: FakeGitLabScenario) -> None:
    fake_gitlab.reset(scenario(fake_gitlab, [result_for(fake_gitlab)]))
    assert publish(fake_gitlab) == "success"
    first = fake_gitlab.snapshot()
    assert publish(fake_gitlab) == "success"
    second = fake_gitlab.snapshot()
    assert first["notes"] == second["notes"]
    assert first["statuses"] == second["statuses"]


@pytest.mark.parametrize("job", [89, 91])
def test_gitlab_rejects_past_and_future_job_results(
    fake_gitlab: FakeGitLabScenario, job: int
) -> None:
    fake_gitlab.reset(scenario(fake_gitlab, [result_for(fake_gitlab, job)]))
    assert publish(fake_gitlab) == "failed"


@pytest.mark.parametrize("status", ["failed", "canceled", "skipped", "manual"])
def test_gitlab_passed_artifact_cannot_override_native_execution(
    fake_gitlab: FakeGitLabScenario, status: str
) -> None:
    fake_gitlab.reset(scenario(fake_gitlab, [result_for(fake_gitlab)], status))
    assert publish(fake_gitlab) == "failed"


def test_gitlab_missing_artifact_is_not_success(fake_gitlab: FakeGitLabScenario) -> None:
    fake_gitlab.reset(scenario(fake_gitlab, []))
    assert publish(fake_gitlab) == "failed"


@pytest.mark.parametrize("reverse", [False, True])
def test_gitlab_conflicting_duplicate_archive_entries_fail_closed(
    fake_gitlab: FakeGitLabScenario, *, reverse: bool
) -> None:
    passed = result_for(fake_gitlab)
    failed = replace(passed, status=ResultStatus.FAILED, failure_kind=FailureKind.COMMAND)
    results = [passed, failed]
    fake_gitlab.reset(scenario(fake_gitlab, list(reversed(results)) if reverse else results))
    assert publish(fake_gitlab) == "failed"


def test_gitlab_identical_duplicate_evidence_is_idempotent(fake_gitlab: FakeGitLabScenario) -> None:
    result = result_for(fake_gitlab)
    fake_gitlab.reset(scenario(fake_gitlab, [result, result]))
    assert publish(fake_gitlab) == "success"


def test_gitlab_rerun_without_artifact_does_not_reuse_previous_success(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    value = scenario(fake_gitlab, [])
    value["artifacts"] = {"1:89": archive([result_for(fake_gitlab, 89)])}
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"
    requests = fake_gitlab.snapshot()["requests"]
    assert isinstance(requests, list)
    assert not any(
        isinstance(request, dict) and "/jobs/89/artifacts" in str(request["path"])
        for request in requests
    )


def test_gitlab_rejects_wrong_execution_project(fake_gitlab: FakeGitLabScenario) -> None:
    result = result_for(fake_gitlab)
    assert isinstance(result.provenance, GitLabProvenance)
    result = replace(result, provenance=replace(result.provenance, project_id=2))
    fake_gitlab.reset(scenario(fake_gitlab, [result]))
    assert publish(fake_gitlab) == "failed"


def test_gitlab_running_job_does_not_publish_its_previous_artifact(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    fake_gitlab.reset(scenario(fake_gitlab, [result_for(fake_gitlab, 89)], "running"))
    assert publish(fake_gitlab) == "pending"
