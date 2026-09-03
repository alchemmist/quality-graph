from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from qg_github.github import GitHubError, HttpGitHubPort, paged

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue
    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


def port(github: FakeGitHubScenario) -> HttpGitHubPort:
    return HttpGitHubPort("owner/repository", "token", base_url=github.base_url)


def test_transport_paginates_comments_files_and_preserves_query_strings(
    fake_github: FakeGitHubScenario,
) -> None:
    comments = [
        {
            "id": identifier,
            "issue_number": 42,
            "body": f"comment-{identifier}",
            "user": {"login": "contributor"},
        }
        for identifier in range(1, 102)
    ]
    files = [
        {"filename": f"src/file-{identifier}.py", "status": "modified", "patch": ""}
        for identifier in range(1, 102)
    ]
    fake_github.reset(
        {
            "comments": cast("JsonValue", comments),
            "pull_files": {"42": cast("JsonValue", files)},
        }
    )

    assert len(paged(port(fake_github), "/issues/42/comments")) == 101
    assert len(paged(port(fake_github), "/pulls/42/files?direction=asc")) == 101

    requests = cast("list[dict[str, JsonValue]]", fake_github.snapshot()["requests"])
    assert sum(request["path"].endswith("/comments") for request in requests) == 2
    assert sum(request["path"].endswith("/files") for request in requests) == 2


@pytest.mark.parametrize(
    ("parameter", "value"),
    [("page", "invalid"), ("page", "0"), ("per_page", "invalid"), ("per_page", "-1")],
)
def test_transport_rejects_invalid_pagination(
    fake_github: FakeGitHubScenario,
    parameter: str,
    value: str,
) -> None:
    response = httpx.get(
        f"{fake_github.base_url}/repos/owner/repository/issues/42/comments",
        params={parameter: value},
    )

    assert response.status_code == 400
    assert response.json() == {"message": "invalid pagination parameters"}


def test_transport_converges_encoded_labels_comments_checks_and_reactions(
    fake_github: FakeGitHubScenario,
) -> None:
    selected = port(fake_github)

    selected.request(
        "POST",
        "/labels",
        {"name": "quality:lint / python", "color": "ff0000", "description": "Failure"},
    )
    selected.request("POST", "/issues/42/labels", {"labels": ["quality:lint / python"]})
    comment = selected.request("POST", "/issues/42/comments", {"body": "pending"})
    assert isinstance(comment, dict)
    comment_id = comment["id"]
    selected.request("PATCH", f"/issues/comments/{comment_id}", {"body": "complete"})
    selected.request(
        "POST",
        f"/issues/comments/{comment_id}/reactions",
        {"content": "hooray"},
    )
    check = selected.request(
        "POST",
        "/check-runs",
        {"name": "Quality Graph", "head_sha": "a" * 40, "status": "in_progress"},
    )
    assert isinstance(check, dict)
    selected.request(
        "PATCH",
        f"/check-runs/{check['id']}",
        {"status": "completed", "conclusion": "success"},
    )
    selected.request("DELETE", "/issues/42/labels/quality%3Alint%20%2F%20python")

    state = fake_github.snapshot()
    comments = cast("list[dict[str, JsonValue]]", state["comments"])
    checks = cast("list[dict[str, JsonValue]]", state["checks"])
    reactions = cast("list[dict[str, JsonValue]]", state["reactions"])
    assert state["issue_labels"] == {"42": []}
    assert comments[0]["body"] == "complete"
    assert checks[0]["conclusion"] == "success"
    assert reactions[0]["content"] == "hooray"


def test_transport_handles_permissions_missing_resources_failures_delays_and_downloads(
    fake_github: FakeGitHubScenario,
) -> None:
    archive = b"PK\x03\x04fixture"
    path = "/repos/owner/repository/pulls/42"
    fake_github.reset(
        {
            "permissions": {"admin": "admin"},
            "downloads": {"7": base64.b64encode(archive).decode()},
            "request_delays": [{"method": "GET", "path": path, "seconds": 0.05}],
            "failures": [
                {
                    "method": "GET",
                    "path": "/repos/owner/repository/pulls/99",
                    "status": 503,
                }
            ],
        }
    )
    selected = port(fake_github)

    started = time.monotonic()
    assert selected.request("GET", "/pulls/42") is not None
    assert time.monotonic() - started >= 0.04
    assert selected.request("GET", "/collaborators/admin/permission") == {"permission": "admin"}
    assert selected.request("GET", "/collaborators/missing/permission") is None
    assert selected.download("/actions/artifacts/7/zip") == archive
    with pytest.raises(GitHubError, match="HTTP 503"):
        selected.request("GET", "/pulls/99")
