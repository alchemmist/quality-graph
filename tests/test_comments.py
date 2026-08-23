import pytest

from qg_github.comments import (
    GITHUB_COMMENT_BODY_LIMIT,
    bounded_comment,
    find_managed_comment,
    marker,
    upsert_managed_comment,
)
from qg_github.github import MemoryGitHubPort


def comments_path(number: int, page: int = 1) -> str:
    return f"/issues/{number}/comments?per_page=100&page={page}"


def test_managed_comment_requires_marker_bot_identity_and_numeric_id() -> None:
    port = MemoryGitHubPort()
    port.enqueue(
        "GET",
        comments_path(42),
        [
            {"id": 1, "body": marker("dashboard"), "user": {"login": "attacker"}},
            {"id": 2, "body": "ordinary", "user": {"login": "github-actions[bot]"}},
            {
                "id": 3,
                "body": marker("dashboard") + "\nbody",
                "user": {"login": "github-actions[bot]"},
            },
        ],
    )

    assert find_managed_comment(port, 42, "dashboard").id == 3


def test_managed_comment_create_and_update_paths() -> None:
    create = MemoryGitHubPort()
    create.enqueue("GET", comments_path(42), [])
    rendered = bounded_comment("dashboard", "Body")
    create.enqueue("POST", "/issues/42/comments", {"id": 10, "body": rendered})

    assert upsert_managed_comment(create, 42, "dashboard", "Body").id == 10

    update = MemoryGitHubPort()
    update.enqueue(
        "GET",
        comments_path(42),
        [{"id": 10, "body": rendered, "user": {"login": "quality-bot"}}],
    )
    update.enqueue("PATCH", "/issues/comments/10", {"id": 10, "body": rendered})
    result = upsert_managed_comment(
        update,
        42,
        "dashboard",
        "Body",
        bot_logins=("quality-bot",),
    )
    assert result.body == rendered


def test_bounded_comment_has_exact_limit_and_omission_notice() -> None:
    value = bounded_comment("dashboard", "x" * (GITHUB_COMMENT_BODY_LIMIT + 100))

    assert len(value) == GITHUB_COMMENT_BODY_LIMIT
    assert value.endswith("characters omitted._\n")


@pytest.mark.parametrize(
    "response",
    [None, {"id": "invalid", "body": "body"}, {"id": 1, "body": 2}],
)
def test_upsert_rejects_invalid_comment_responses(response: object) -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", comments_path(42), [])
    port.enqueue("POST", "/issues/42/comments", response)
    with pytest.raises(TypeError):
        upsert_managed_comment(port, 42, "dashboard", "Body")
