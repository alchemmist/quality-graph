from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_gitlab.api import GitLabError, HttpGitLab
from qg_gitlab.compiler import graph_digest
from qg_gitlab.publication import publish_merge_request
from quality_graph_core.graph import Graph
from quality_graph_core.result import (
    FailureKind,
    Finding,
    GitLabProvenance,
    ResultStatus,
    Severity,
    SourceLocation,
)
from tests.integration.test_gitlab_publication_http import archive, result_for, scenario

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue
    from tests.integration.fake_gitlab import FakeGitLabScenario

pytestmark = pytest.mark.integration
STAMP = "2026-01-01T00:00:00Z"


def obj(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def command_note(body: str, identity: int = 10) -> dict[str, JsonValue]:
    return {
        "id": identity,
        "body": body,
        "author": {"id": 9},
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def configured(
    fake: FakeGitLabScenario,
    body: str = "/qg ignore finding",
    access: int = 40,
    *,
    node_label: bool = False,
) -> dict[str, JsonValue]:
    value = scenario(fake, [])
    resources = obj(value["resources"])
    source = yaml.safe_load(str(resources["/projects/1/repository/files/qg.yaml/raw"]))
    source["nodes"]["quality"]["policy"] = {
        "approvals": {"findings": True, "files": True, "node": True}
    }
    if node_label:
        source["nodes"]["quality"]["label"] = {
            "name": "qg:node",
            "color": "ff0000",
            "description": "Node failure",
        }
    source["labels"] = {
        "enabled": True,
        "failing": {"name": "qg:failed", "color": "ff0000", "description": "Quality Graph failure"},
    }
    text = yaml.safe_dump(source)
    resources["/projects/1/repository/files/qg.yaml/raw"] = text
    resources["/projects/1/members/all/9"] = {"id": 9, "access_level": access}
    result = result_for(fake)
    assert isinstance(result.provenance, GitLabProvenance)
    result = replace(
        result,
        status=ResultStatus.FAILED,
        failure_kind=FailureKind.QUALITY,
        provenance=replace(result.provenance, graph_digest=graph_digest(Graph.from_yaml(text))),
        findings=(
            Finding(
                "finding",
                Severity.ERROR,
                "A quality finding",
                location=SourceLocation("src.py", 1, 1),
            ),
        ),
    )
    value["artifacts"] = {"1:90": archive([result])}
    jobs = obj(value["jobs"])["1:200"]
    assert isinstance(jobs, list)
    obj(jobs[0]).update(status="failed", allow_failure=True)
    value["notes"] = {"1:4": [command_note(body)]}
    obj(obj(value["merge_requests"])["1:4"])["labels"] = ["unrelated"]
    return value


def publish(fake: FakeGitLabScenario) -> str:
    with HttpGitLab(fake.base_url, "publisher-token") as api:
        result = publish_merge_request(api, 1, 4)
    assert result is not None
    return result.state


@pytest.mark.parametrize(
    "command", ["/qg ignore finding", "/qg ignore-file src.py", "/qg ignore quality"]
)
def test_authorized_approvals_are_idempotent_and_preserve_other_labels(
    fake_gitlab: FakeGitLabScenario, command: str
) -> None:
    fake_gitlab.reset(configured(fake_gitlab, command))
    assert publish(fake_gitlab) == "success"
    before = fake_gitlab.snapshot()
    assert publish(fake_gitlab) == "success"
    after = fake_gitlab.snapshot()
    assert before["notes"] == after["notes"]
    assert obj(obj(after["merge_requests"])["1:4"])["labels"] == ["unrelated"]
    requests = after["requests"]
    assert isinstance(requests, list)
    assert not any("/retry" in str(obj(request)["path"]) for request in requests)


@pytest.mark.parametrize("access", [0, 10, 20, 30])
def test_unauthorized_commands_cannot_approve_a_quality_failure(
    fake_gitlab: FakeGitLabScenario, access: int
) -> None:
    fake_gitlab.reset(configured(fake_gitlab, access=access))
    assert publish(fake_gitlab) == "failed"
    value = fake_gitlab.snapshot()
    assert obj(obj(value["merge_requests"])["1:4"])["labels"] == ["qg:failed", "unrelated"]


@pytest.mark.parametrize(
    "body",
    [
        "/qg ignore unknown",
        "/qg ignore finding,unknown",
        "/qg ignore",
        "/qg ignore-file unknown",
        "/qg help",
        "/qg status",
        "plain comment",
    ],
)
def test_unknown_targets_and_read_only_commands_do_not_approve(
    fake_gitlab: FakeGitLabScenario, body: str
) -> None:
    fake_gitlab.reset(configured(fake_gitlab, body))
    assert publish(fake_gitlab) == "failed"


def test_edited_commands_are_not_authorization(fake_gitlab: FakeGitLabScenario) -> None:
    value = configured(fake_gitlab)
    note = command_note("/qg ignore finding")
    note["updated_at"] = "2026-01-02T00:00:00Z"
    value["notes"] = {"1:4": [note]}
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"


@pytest.mark.parametrize("status", [403, 404])
def test_missing_membership_fails_closed(fake_gitlab: FakeGitLabScenario, status: int) -> None:
    value = configured(fake_gitlab)
    value["failures"] = {"GET /api/v4/projects/1/members/all/9": status}
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"


def test_approval_revoke_removes_only_the_matching_semantic_target(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    fake_gitlab.reset(configured(fake_gitlab))
    assert publish(fake_gitlab) == "success"
    value = fake_gitlab.snapshot()
    notes = obj(value["notes"])["1:4"]
    assert isinstance(notes, list)
    notes.append(command_note("/qg remove-ignore finding", 100))
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"
    assert publish(fake_gitlab) == "failed"


@pytest.mark.parametrize(
    "change", ["author", "edited", "project", "mr", "version", "malformed", "missing-actor"]
)
def test_forged_or_mismatched_ledger_records_cannot_grant_approval(
    fake_gitlab: FakeGitLabScenario, change: str
) -> None:
    value = configured(fake_gitlab, "plain comment")
    record = {
        "version": 1,
        "projectId": 1,
        "mergeRequest": 4,
        "actorId": 9,
        "sourceNoteId": 5,
        "operation": "add",
        "targets": ["finding:finding"],
    }
    if change == "project":
        record["projectId"] = 2
    elif change == "mr":
        record["mergeRequest"] = 5
    elif change == "version":
        record["version"] = 99
    elif change == "missing-actor":
        record.pop("actorId")
    payload = json.dumps(record, separators=(",", ":")) if change != "malformed" else "{invalid}"
    note = {
        "id": 20,
        "author": {"id": 8 if change == "author" else 7},
        "body": f"<!-- quality-graph:gitlab:record:{payload} -->",
        "created_at": STAMP,
        "updated_at": STAMP if change != "edited" else "later",
    }
    value["notes"] = {"1:4": [note]}
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"


@pytest.mark.parametrize("targets", [None, [1], ["unknown:target"]])
def test_malformed_ledger_targets_do_not_authorize_changes(
    fake_gitlab: FakeGitLabScenario, targets: JsonValue
) -> None:
    value = configured(fake_gitlab, "plain comment")
    record = {
        "version": 1,
        "projectId": 1,
        "mergeRequest": 4,
        "actorId": 9,
        "sourceNoteId": 5,
        "operation": "add",
        "targets": targets,
    }
    value["notes"] = {
        "1:4": [
            {
                "id": 20,
                "author": {"id": 7},
                "body": "<!-- quality-graph:gitlab:record:" + json.dumps(record) + " -->",
                "created_at": STAMP,
                "updated_at": STAMP,
            }
        ]
    }
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"


def test_duplicate_command_records_do_not_reapply_an_old_approval(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    fake_gitlab.reset(configured(fake_gitlab))
    assert publish(fake_gitlab) == "success"
    value = fake_gitlab.snapshot()
    notes = obj(value["notes"])["1:4"]
    assert isinstance(notes, list)
    record = next(
        obj(note) for note in notes if "quality-graph:gitlab:record:" in str(obj(note).get("body"))
    )
    notes.append({**record, "id": 99})
    notes.append(command_note("/qg remove-ignore finding", 100))
    fake_gitlab.reset(value)
    assert publish(fake_gitlab) == "failed"


def test_membership_server_failure_is_not_a_denial_or_approval(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    value = configured(fake_gitlab)
    value["failures"] = {"GET /api/v4/projects/1/members/all/9": 400}
    fake_gitlab.reset(value)
    with pytest.raises(GitLabError):
        publish(fake_gitlab)


def test_node_labels_are_owned_and_malformed_label_state_is_rejected(
    fake_gitlab: FakeGitLabScenario,
) -> None:
    fake_gitlab.reset(configured(fake_gitlab, "plain comment", node_label=True))
    assert publish(fake_gitlab) == "failed"
    assert obj(obj(fake_gitlab.snapshot()["merge_requests"])["1:4"])["labels"] == [
        "qg:failed",
        "qg:node",
        "unrelated",
    ]
    value = configured(fake_gitlab, "plain comment")
    obj(obj(value["merge_requests"])["1:4"])["labels"] = "invalid"
    fake_gitlab.reset(value)
    with pytest.raises(TypeError, match="labels must be an array"):
        publish(fake_gitlab)
