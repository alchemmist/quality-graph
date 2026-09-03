from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from qg_github.approvals import (
    ApprovalRecord,
    append_approval_record,
    approval_ledger,
    record_marker,
)
from qg_github.comments import bounded_comment, upsert_managed_comment
from qg_github.github import HttpGitHubPort
from qg_github.labels import reconcile_labels
from quality_graph_core.graph import Graph
from quality_graph_core.policy import ApprovalTarget
from quality_graph_core.result import (
    ControlKind,
    FailureKind,
    JsonValue,
    Provenance,
    Result,
    ResultStatus,
)
from tests.test_graph import GRAPH

if TYPE_CHECKING:
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def port(github: FakeGitHubScenario) -> HttpGitHubPort:
    return HttpGitHubPort("owner/repository", "token", base_url=github.base_url)


def test_managed_comment_converges_without_noop_patch_and_ignores_foreign_marker(
    fake_github: FakeGitHubScenario,
) -> None:
    marker = "<!-- quality-graph:dashboard -->\n\nforeign\n"
    fake_github.reset(
        {
            "comments": [
                {
                    "id": 1,
                    "issue_number": 42,
                    "body": marker,
                    "user": {"login": "contributor"},
                }
            ]
        }
    )

    created = upsert_managed_comment(port(fake_github), 42, "dashboard", "Body")
    unchanged = upsert_managed_comment(port(fake_github), 42, "dashboard", "Body")
    updated = upsert_managed_comment(port(fake_github), 42, "dashboard", "Updated")
    state = fake_github.snapshot()

    assert created.id == unchanged.id == updated.id
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    assert len(comments) == 2
    assert comments[0]["body"] == marker
    assert comments[1]["body"] == bounded_comment("dashboard", "Updated")
    requests = cast("list[dict[str, JsonValue]]", state["requests"])
    assert sum(request["method"] == "PATCH" for request in requests) == 1


def test_approval_ledger_replays_only_immutable_bot_records_over_http(
    fake_github: FakeGitHubScenario,
) -> None:
    finding = ApprovalTarget(ControlKind.FINDING, "finding")
    trusted = ApprovalRecord(add=True, targets=(finding,), actor="admin", source_comment_id=10)
    forged = ApprovalRecord(
        add=False,
        targets=(finding,),
        actor="contributor",
        source_comment_id=11,
    )
    fake_github.reset(
        {
            "comments": [
                {
                    "id": 1,
                    "issue_number": 42,
                    "body": record_marker(forged),
                    "user": {"login": "contributor"},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": 2,
                    "issue_number": 42,
                    "body": record_marker(trusted),
                    "user": {"login": "github-actions[bot]"},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": 3,
                    "issue_number": 42,
                    "body": record_marker(forged),
                    "user": {"login": "github-actions[bot]"},
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                },
            ]
        }
    )

    assert approval_ledger(port(fake_github), 42) == frozenset({finding})
    record_id = append_approval_record(
        port(fake_github),
        42,
        ApprovalRecord(add=False, targets=(finding,), actor="admin", source_comment_id=12),
    )
    assert record_id == 4
    assert approval_ledger(port(fake_github), 42) == frozenset()


def test_label_reconciliation_preserves_foreign_state_and_removes_owned_state(
    fake_github: FakeGitHubScenario,
) -> None:
    graph = Graph.from_yaml(GRAPH)
    failed = Result(
        "lint",
        "Lint",
        ResultStatus.FAILED,
        Provenance("owner/repository", "a" * 40, 10, 1, "b" * 64, 42),
        FailureKind.QUALITY,
    )
    fake_github.reset(
        {
            "labels": [{"name": "foreign", "color": "ffffff", "description": None}],
            "issue_labels": {"42": ["foreign", "quality:recovered"]},
        }
    )

    reconcile_labels(
        port(fake_github),
        42,
        graph,
        {"lint": failed},
        previous_owned={"quality:recovered"},
    )
    state = fake_github.snapshot()

    assert state["issue_labels"] == {"42": ["foreign", "quality:failed", "quality:lint"]}
    labels = cast("list[dict[str, JsonValue]]", state["labels"])
    assert {label["name"] for label in labels} == {"foreign", "quality:lint"}
