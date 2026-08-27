"""Manage one bounded bot-owned Quality Graph pull-request comment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from qg_github.github import GitHubPort, paged

if TYPE_CHECKING:
    from collections.abc import Iterable

    from quality_graph_core.result import JsonValue

GITHUB_COMMENT_BODY_LIMIT = 65_536
DEFAULT_BOT_LOGIN = "github-actions[bot]"


@dataclass(frozen=True)
class ManagedComment:
    """Carry the identity and body of one trusted managed comment."""

    id: int
    body: str


def marker(name: str) -> str:
    """Render one stable Quality Graph hidden marker."""
    return f"<!-- quality-graph:{name} -->"


def bounded_comment(name: str, body: str) -> str:
    """Wrap and bound one managed comment with an exact omission notice."""
    value = f"{marker(name)}\n\n{body.rstrip()}\n"
    if len(value) <= GITHUB_COMMENT_BODY_LIMIT:
        return value
    omitted = len(value) - GITHUB_COMMENT_BODY_LIMIT
    while True:
        notice = f"\n\n_Report truncated; {omitted} characters omitted._\n"
        prefix = GITHUB_COMMENT_BODY_LIMIT - len(notice)
        updated = len(value) - prefix
        if updated == omitted:
            return value[:prefix] + notice
        omitted = updated


def find_managed_comment(
    port: GitHubPort,
    number: int,
    name: str,
    *,
    bot_logins: Iterable[str] = (DEFAULT_BOT_LOGIN,),
) -> ManagedComment | None:
    """Return the bot-owned comment carrying the requested marker."""
    expected = marker(name)
    trusted = set(bot_logins)
    for item in paged(port, f"/issues/{number}/comments"):
        body = item.get("body")
        author = item.get("user")
        comment_id = item.get("id")
        login = author.get("login") if isinstance(author, dict) else None
        if (
            isinstance(body, str)
            and expected in body
            and login in trusted
            and isinstance(comment_id, int)
            and not isinstance(comment_id, bool)
        ):
            return ManagedComment(comment_id, body)
    return None


def upsert_managed_comment(
    port: GitHubPort,
    number: int,
    name: str,
    body: str,
    *,
    bot_logins: Iterable[str] = (DEFAULT_BOT_LOGIN,),
) -> ManagedComment:
    """Create or replace one trusted managed comment."""
    rendered = bounded_comment(name, body)
    existing = find_managed_comment(port, number, name, bot_logins=bot_logins)
    if existing is None:
        response = port.request("POST", f"/issues/{number}/comments", {"body": rendered})
    elif existing.body == rendered:
        return existing
    else:
        response = port.request("PATCH", f"/issues/comments/{existing.id}", {"body": rendered})
    data = _object(response, "managed comment")
    comment_id = data.get("id")
    response_body = data.get("body")
    if not isinstance(comment_id, int) or isinstance(comment_id, bool):
        message = "managed comment response has no numeric id"
        raise TypeError(message)
    if not isinstance(response_body, str):
        message = "managed comment response has no body"
        raise TypeError(message)
    return ManagedComment(comment_id, response_body)


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value
