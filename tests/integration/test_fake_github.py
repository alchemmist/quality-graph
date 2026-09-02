from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from qg_github.github import GitHubError, HttpGitHubPort, paged
from tests.integration.fake_github import FakeGitHubServer

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue

pytestmark = pytest.mark.integration


def test_stateful_service_converges_repository_state_over_real_http() -> None:
    comments = [
        {
            "id": identifier,
            "issue_number": 42,
            "body": f"comment-{identifier}",
            "user": {"login": "contributor"},
        }
        for identifier in range(1, 102)
    ]
    with FakeGitHubServer() as github:
        github.reset(
            {
                "comments": cast("JsonValue", comments),
                "permissions": {"admin": "admin"},
                "failures": [
                    {
                        "method": "GET",
                        "path": "/repos/owner/repository/pulls/99",
                        "status": 503,
                    }
                ],
            }
        )
        port = HttpGitHubPort("owner/repository", "token", base_url=github.base_url)

        assert len(paged(port, "/issues/42/comments")) == 101
        port.request("POST", "/issues/42/labels", {"labels": ["quality:failed"]})
        with pytest.raises(GitHubError, match="HTTP 503"):
            port.request("GET", "/pulls/99")

        state = github.snapshot()

    assert state["issue_labels"] == {"42": ["quality:failed"]}
    requests = cast("list[dict[str, JsonValue]]", state["requests"])
    assert requests[-1] == {"method": "GET", "path": "/repos/owner/repository/pulls/99"}
    assert sum(request["path"].endswith("/comments") for request in requests) == 2
