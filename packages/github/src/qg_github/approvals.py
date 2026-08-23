"""Persist authorized approval changes as immutable bot-owned comment records."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from qg_github.comments import DEFAULT_BOT_LOGIN
from qg_github.github import GitHubPort, paged
from quality_graph_core.policy import ApprovalTarget

if TYPE_CHECKING:
    from collections.abc import Iterable

    from quality_graph_core.result import JsonValue

LEDGER_RE = re.compile(r"<!-- quality-graph:approval:(?P<payload>[A-Za-z0-9_-]+) -->")


@dataclass(frozen=True)
class ApprovalRecord:
    """Describe one authorized append-only approval change."""

    add: bool
    targets: tuple[ApprovalTarget, ...]
    actor: str
    source_comment_id: int

    def __post_init__(self) -> None:
        """Reject empty or duplicate record targets."""
        if not self.targets or len(set(self.targets)) != len(self.targets):
            message = "approval record targets must be non-empty and unique"
            raise ValueError(message)
        if not self.actor:
            message = "approval record actor must not be empty"
            raise ValueError(message)
        if self.source_comment_id < 1:
            message = "approval source comment id must be positive"
            raise ValueError(message)

    def to_json(self) -> str:
        """Serialize a record canonically for its immutable marker."""
        value: dict[str, JsonValue] = {
            "version": 0,
            "operation": "add" if self.add else "remove",
            "targets": [target.key for target in self.targets],
            "actor": self.actor,
            "sourceCommentId": self.source_comment_id,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, value: str) -> ApprovalRecord:
        """Parse a record from untrusted marker JSON."""
        data = _object(cast("JsonValue", json.loads(value)), "approval record")
        if data.get("version") != 0:
            message = "unsupported approval record version"
            raise ValueError(message)
        operation = _string(data.get("operation"), "approval operation")
        if operation not in {"add", "remove"}:
            message = f"unknown approval operation: {operation}"
            raise ValueError(message)
        targets = tuple(
            ApprovalTarget.from_key(_string(item, "approval target"))
            for item in _array(data.get("targets"), "approval targets")
        )
        return cls(
            operation == "add",
            targets,
            _string(data.get("actor"), "approval actor"),
            _integer(data.get("sourceCommentId"), "approval source comment id"),
        )


def record_marker(record: ApprovalRecord) -> str:
    """Encode one authorization record in a stable hidden marker."""
    encoded = base64.urlsafe_b64encode(record.to_json().encode()).decode().rstrip("=")
    return f"<!-- quality-graph:approval:{encoded} -->"


def decode_record(body: str) -> ApprovalRecord | None:
    """Decode a canonical record marker from one bot comment."""
    match = LEDGER_RE.search(body)
    if match is None:
        return None
    encoded = match.group("payload")
    try:
        value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        return ApprovalRecord.from_json(value)
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def approval_ledger(
    port: GitHubPort,
    number: int,
    *,
    bot_logins: Iterable[str] = (DEFAULT_BOT_LOGIN,),
) -> frozenset[ApprovalTarget]:
    """Replay trusted immutable authorization records in comment order."""
    trusted = set(bot_logins)
    approvals: set[ApprovalTarget] = set()
    comments = sorted(
        paged(port, f"/issues/{number}/comments"),
        key=lambda item: _integer(item.get("id"), "approval comment id"),
    )
    for comment in comments:
        author = comment.get("user")
        login = author.get("login") if isinstance(author, dict) else None
        created = comment.get("created_at")
        updated = comment.get("updated_at")
        body = comment.get("body")
        if login not in trusted or created != updated or not isinstance(body, str):
            continue
        record = decode_record(body)
        if record is None:
            continue
        if record.add:
            approvals.update(record.targets)
        else:
            approvals.difference_update(record.targets)
    return frozenset(approvals)


def append_approval_record(
    port: GitHubPort,
    number: int,
    record: ApprovalRecord,
) -> int:
    """Create one transparent bot-owned immutable authorization record."""
    action = "approved" if record.add else "removed approval for"
    targets = ", ".join(f"`{target.key}`" for target in record.targets)
    body = (
        f"Quality Graph recorded that @{record.actor} {action} {targets}.\n\n"
        f"{record_marker(record)}"
    )
    response = _object(
        port.request("POST", f"/issues/{number}/comments", {"body": body}),
        "approval comment",
    )
    return _integer(response.get("id"), "approval comment id")


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise TypeError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    return value


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise TypeError(message)
    return value
