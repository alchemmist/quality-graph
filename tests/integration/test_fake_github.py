from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from qg_github.github import GitHubError, HttpGitHubPort, paged
from tests.integration.fake_github import FakeGitHubServer

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue
    from tests.integration.fake_github import FakeGitHubScenario

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


def test_default_workflow_run_is_explicitly_in_progress() -> None:
    with FakeGitHubServer() as github:
        selected = HttpGitHubPort("owner/repository", "token", base_url=github.base_url)

        run = selected.request("GET", "/actions/runs/10")

    assert isinstance(run, dict)
    assert run["status"] == "in_progress"


def test_workflow_job_pages_share_one_snapshot_per_poll() -> None:
    first = [
        {"name": f"Job {identifier}", "status": "completed", "conclusion": "success"}
        for identifier in range(1, 102)
    ]
    second = [{"name": "Next poll", "status": "in_progress"}]
    with FakeGitHubServer() as github:
        github.reset(
            {
                "workflow_job_snapshots": {"10": [first, second]},
            }
        )
        selected = HttpGitHubPort("owner/repository", "token", base_url=github.base_url)

        page_one = selected.request(
            "GET",
            "/actions/runs/10/jobs?filter=all&per_page=100&page=1",
        )
        page_two = selected.request(
            "GET",
            "/actions/runs/10/jobs?filter=all&per_page=100&page=2",
        )
        next_poll = selected.request(
            "GET",
            "/actions/runs/10/jobs?filter=all&per_page=100&page=1",
        )

    assert isinstance(page_one, dict)
    assert isinstance(page_two, dict)
    assert isinstance(next_poll, dict)
    assert len(cast("list[JsonValue]", page_one["jobs"])) == 100
    assert cast("list[dict[str, JsonValue]]", page_two["jobs"])[0]["name"] == "Job 101"
    assert cast("list[dict[str, JsonValue]]", next_poll["jobs"])[0]["name"] == "Next poll"


@pytest.mark.parametrize("selection", ["latest", "all", "attempt"])
def test_attempt_job_history_filters_before_pagination(
    fake_github: FakeGitHubScenario, selection: str
) -> None:
    old = [{"id": 1, "run_attempt": 2, "name": "Retained", "conclusion": "success"}]
    current = [
        {"id": identifier, "run_attempt": 3, "name": f"Job {identifier}"}
        for identifier in range(2, 103)
    ]
    fake_github.reset({"workflow_attempt_jobs": {"10": {"2": old, "3": current}}})
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    path = (
        "/actions/runs/10/attempts/3/jobs"
        if selection == "attempt"
        else f"/actions/runs/10/jobs?filter={selection}"
    )
    separator = "&" if "?" in path else "?"
    first = port.request("GET", f"{path}{separator}per_page=100&page=1")
    second = port.request("GET", f"{path}{separator}per_page=100&page=2")
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    expected = old + current if selection == "all" else current
    assert first["total_count"] == len(expected)
    assert first["jobs"] + second["jobs"] == expected
    assert port.request("GET", "/actions/runs/10/attempts/2/jobs") == {
        "total_count": 1,
        "jobs": old,
    }
    assert port.request("GET", "/actions/runs/10/attempts/4/jobs") is None
    assert fake_github.snapshot()["workflow_attempt_jobs"] == {"10": {"2": old, "3": current}}
    fake_github.reset()
    assert fake_github.snapshot()["workflow_attempt_jobs"] == {}
