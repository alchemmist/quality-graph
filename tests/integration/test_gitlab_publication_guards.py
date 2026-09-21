from __future__ import annotations

import copy
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_gitlab.api import HttpGitLab
from qg_gitlab.publication import publish_merge_request
from qg_gitlab.runtime import main
from tests.integration.test_gitlab_publication_http import HEAD, result_for, scenario

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue
    from tests.integration.fake_gitlab import FakeGitLabScenario

pytestmark = pytest.mark.integration


def obj(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def publish(fake: FakeGitLabScenario) -> str | None:
    with HttpGitLab(fake.base_url, "publisher-token") as api:
        outcome = publish_merge_request(api, 1, 4)
    return outcome.state if outcome is not None else None


@pytest.mark.parametrize(
    ("status", "expected"), [("failed", "failed"), ("running", "pending"), ("canceled", "canceled")]
)
def test_native_admission_failure_cannot_be_hidden_by_successful_checks(
    fake_gitlab: FakeGitLabScenario, status: str, expected: str
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    jobs = obj(value["jobs"])["1:200"]
    assert isinstance(jobs, list)
    obj(jobs[1])["status"] = status
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == expected


@pytest.mark.parametrize("reads", [1, 2, 3])
def test_a_new_head_prevents_stale_summary_writes(
    fake_gitlab: FakeGitLabScenario, reads: int
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    current = obj(obj(value["merge_requests"])["1:4"])
    changed = {**current, "sha": "b" * 40, "head_pipeline": {"id": 201, "project_id": 1}}
    value["mr_snapshots"] = {"1:4": [*[copy.deepcopy(current) for _ in range(reads)], changed]}
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) is None
    assert obj(fake_gitlab.snapshot()["notes"])["1:4"] == []


@pytest.mark.parametrize(
    "change", ["closed", "no-pipeline", "branch-pipeline", "different-default", "no-mr-flow"]
)
def test_publication_only_handles_its_supported_current_mr_scope(
    fake_gitlab: FakeGitLabScenario, change: str
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    mr = obj(obj(value["merge_requests"])["1:4"])
    resources = obj(value["resources"])
    if change == "closed":
        mr["state"] = "closed"
    elif change == "no-pipeline":
        mr["head_pipeline"] = None
    elif change == "branch-pipeline":
        obj(resources["/projects/1/pipelines/200"])["source"] = "push"
    else:
        source = yaml.safe_load(str(resources["/projects/1/repository/files/qg.yaml/raw"]))
        if change == "different-default":
            source["provider"]["configuration"]["default-branch"] = "trunk"
        else:
            source["nodes"]["quality"]["events"] = ["push"]
        resources["/projects/1/repository/files/qg.yaml/raw"] = yaml.safe_dump(source)
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) is None


@pytest.mark.parametrize(
    "change", ["pipeline-sha", "unprotected", "foreign-server", "foreign-publisher"]
)
def test_publication_rejects_untrusted_governance_and_pipeline_metadata(
    fake_gitlab: FakeGitLabScenario, change: str
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    resources = obj(value["resources"])
    if change == "pipeline-sha":
        obj(resources["/projects/1/pipelines/200"])["sha"] = "b" * 40
    elif change == "unprotected":
        obj(resources["/projects/1/repository/branches/main"])["protected"] = False
    else:
        source = yaml.safe_load(str(resources["/projects/1/repository/files/qg.yaml/raw"]))
        settings = source["provider"]["configuration"]
        if change == "foreign-server":
            settings["server-url"] = "https://another.example.test"
        else:
            settings["publisher-user-id"] = 8
        resources["/projects/1/repository/files/qg.yaml/raw"] = yaml.safe_dump(source)
    fake_gitlab.reset(value)
    with pytest.raises(ValueError, match=r"GitLab|publisher"):
        publish(fake_gitlab)


@pytest.mark.parametrize("change", ["foreign-owner", "duplicates", "bad-response"])
def test_status_publication_requires_unambiguous_owned_confirmation(
    fake_gitlab: FakeGitLabScenario, change: str
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    status = {
        "name": "Quality Graph",
        "pipeline_id": 200,
        "sha": HEAD,
        "status": "success",
        "author": {"id": 8 if change == "foreign-owner" else 7},
    }
    if change == "bad-response":
        value["status_response"] = {**status, "status": "failed"}
    else:
        value["statuses"] = [status, status] if change == "duplicates" else [status]
    fake_gitlab.reset(value)
    with pytest.raises(ValueError, match=r"owned|ambiguous|confirm"):
        publish(fake_gitlab)
    assert obj(fake_gitlab.snapshot()["notes"])["1:4"] == []


def test_other_pipeline_statuses_cannot_satisfy_current_publication(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    value["statuses"] = [
        {
            "name": "Other check",
            "pipeline_id": 200,
            "sha": HEAD,
            "status": "success",
            "author": {"id": 8},
        },
        {
            "name": "Quality Graph",
            "pipeline_id": 199,
            "sha": HEAD,
            "status": "success",
            "author": {"id": 7},
        },
    ]
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "success"
    statuses = fake_gitlab.snapshot()["statuses"]
    assert isinstance(statuses, list)
    assert len(statuses) == 3


@pytest.mark.parametrize("override", [False, True])
def test_installed_publisher_entrypoint_reconciles_only_allowed_projects(
    fake_gitlab: FakeGitLabScenario, monkeypatch: pytest.MonkeyPatch, *, override: bool
) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    obj(value["resources"])["/projects/1/merge_requests"] = [{"iid": 4}]
    fake_gitlab.reset(value)
    monkeypatch.setenv("QG_GITLAB_PROJECTS", "[1]")
    monkeypatch.setenv("QG_GITLAB_TOKEN", "publisher-token")
    monkeypatch.setenv("CI_SERVER_URL", fake_gitlab.base_url)
    monkeypatch.delenv("CI_API_V4_URL", raising=False)
    if override:
        monkeypatch.setenv("QG_GITLAB_SERVER_URL", fake_gitlab.base_url)
        monkeypatch.setenv("QG_GITLAB_API_URL", fake_gitlab.base_url + "/api/v4")
    assert main(["publish"]) == 0


def test_publisher_configuration_and_failures_are_not_silent(
    fake_gitlab: FakeGitLabScenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QG_GITLAB_PROJECTS", "{}")
    with pytest.raises(ValueError, match="nonempty array"):
        main(["publish"])
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    resources = obj(value["resources"])
    resources["/projects/1/merge_requests"] = [{"iid": 4}]
    obj(resources["/projects/1/repository/branches/main"])["protected"] = False
    fake_gitlab.reset(value)
    monkeypatch.setenv("QG_GITLAB_PROJECTS", "[1]")
    monkeypatch.setenv("QG_GITLAB_TOKEN", "publisher-token")
    monkeypatch.setenv("CI_SERVER_URL", fake_gitlab.base_url)
    monkeypatch.delenv("CI_API_V4_URL", raising=False)
    assert main(["publish"]) == 1


def test_ambiguous_owned_summaries_are_not_overwritten(fake_gitlab: FakeGitLabScenario) -> None:
    value = scenario(fake_gitlab, [result_for(fake_gitlab)])
    value["notes"] = {
        "1:4": [
            {"id": identity, "author": {"id": 7}, "body": "<!-- quality-graph:gitlab:summary -->"}
            for identity in (1, 2)
        ]
    }
    fake_gitlab.reset(value)
    with pytest.raises(ValueError, match="ambiguous publisher-owned"):
        publish(fake_gitlab)


def test_canceled_pipeline_with_skipped_dependents_remains_canceled(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    value = scenario(fake_gitlab, [], "skipped")
    jobs = obj(value["jobs"])["1:200"]
    assert isinstance(jobs, list)
    obj(jobs[1])["status"] = "canceled"
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "canceled"
