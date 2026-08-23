import base64
import json

import pytest

from quality_graph.approvals import (
    ApprovalRecord,
    append_approval_record,
    approval_ledger,
    decode_record,
    record_marker,
)
from quality_graph.github import MemoryGitHubPort
from quality_graph.policy import ApprovalTarget
from quality_graph.result import ControlKind


def target(value: str = "finding") -> ApprovalTarget:
    return ApprovalTarget(ControlKind.FINDING, value)


def record(*, add: bool = True, value: str = "finding") -> ApprovalRecord:
    return ApprovalRecord(add, (target(value),), "admin", 10)


def comments_path() -> str:
    return "/issues/42/comments?per_page=100&page=1"


def ledger_comment(comment_id: int, value: ApprovalRecord) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": record_marker(value),
        "user": {"login": "github-actions[bot]"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def test_approval_record_round_trips_canonical_marker() -> None:
    value = record()

    assert ApprovalRecord.from_json(value.to_json()) == value
    assert decode_record(record_marker(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        ApprovalRecord,
    ],
)
def test_approval_record_validation(value: type[ApprovalRecord]) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        value(add=True, targets=(), actor="admin", source_comment_id=1)
    with pytest.raises(ValueError, match="unique"):
        value(
            add=True,
            targets=(target(), target()),
            actor="admin",
            source_comment_id=1,
        )
    with pytest.raises(ValueError, match="actor"):
        value(add=True, targets=(target(),), actor="", source_comment_id=1)
    with pytest.raises(ValueError, match="positive"):
        value(add=True, targets=(target(),), actor="admin", source_comment_id=0)


def test_ledger_replays_add_remove_and_ignores_untrusted_or_edited_records() -> None:
    port = MemoryGitHubPort()
    edited = ledger_comment(3, record(value="edited"))
    edited["updated_at"] = "2026-01-02T00:00:00Z"
    attacker = ledger_comment(4, record(value="attacker"))
    attacker["user"] = {"login": "attacker"}
    port.enqueue(
        "GET",
        comments_path(),
        [
            ledger_comment(2, record(add=False)),
            ledger_comment(1, record()),
            edited,
            attacker,
            {"id": 5, "body": "ordinary", "user": {"login": "github-actions[bot]"}},
        ],
    )

    assert approval_ledger(port, 42) == frozenset()


def test_append_record_creates_transparent_bot_comment() -> None:
    port = MemoryGitHubPort()
    port.enqueue("POST", "/issues/42/comments", {"id": 100})

    assert append_approval_record(port, 42, record()) == 100
    body = port.requests[0][2]["body"]
    assert "@admin approved `finding:finding`" in body
    assert "<!-- quality-graph:approval:" in body


def test_record_decoder_rejects_forged_payloads() -> None:
    assert decode_record("ordinary") is None
    assert decode_record("<!-- quality-graph:approval:invalid -->") is None

    invalid = base64.urlsafe_b64encode(
        json.dumps(
            {
                "version": 1,
                "operation": "add",
                "targets": ["finding:finding"],
                "actor": "admin",
                "sourceCommentId": 1,
            }
        ).encode()
    ).decode()
    assert decode_record(f"<!-- quality-graph:approval:{invalid} -->") is None


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("[]", TypeError),
        ('{"version":1}', ValueError),
        (
            '{"version":0,"operation":"unknown","targets":[],"actor":"admin","sourceCommentId":1}',
            ValueError,
        ),
        (
            '{"version":0,"operation":"add","targets":{},"actor":"admin","sourceCommentId":1}',
            TypeError,
        ),
        (
            '{"version":0,"operation":"add","targets":["finding:finding"],'
            '"actor":1,"sourceCommentId":1}',
            TypeError,
        ),
        (
            '{"version":0,"operation":"add","targets":["finding:finding"],'
            '"actor":"admin","sourceCommentId":false}',
            TypeError,
        ),
    ],
)
def test_approval_record_narrows_untrusted_json(value: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        ApprovalRecord.from_json(value)


def test_append_record_rejects_invalid_comment_response() -> None:
    port = MemoryGitHubPort()
    port.enqueue("POST", "/issues/42/comments", None)
    with pytest.raises(TypeError, match="approval comment"):
        append_approval_record(port, 42, record())
