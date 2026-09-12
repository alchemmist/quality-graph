from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from qg_gitlab import api as gitlab_api
from qg_gitlab.api import GitLabError, HttpGitLab, integer, object_value, server_url, string

if TYPE_CHECKING:
    from collections.abc import Iterator


def test_gitlab_api_preserves_self_managed_prefix_and_nested_namespace() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 1})

    with HttpGitLab(
        "https://gitlab.example.test/gitlab", "test-token", transport=httpx.MockTransport(respond)
    ) as api:
        assert api.request("GET", api.project_path("group/team/project")) == {"id": 1}
    assert requests[0].url.raw_path == b"/gitlab/api/v4/projects/group%2Fteam%2Fproject"
    assert requests[0].headers["PRIVATE-TOKEN"] == "test-token"


def test_artifact_redirect_does_not_forward_gitlab_credentials_or_cookies() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "gitlab.example.test":
            return httpx.Response(
                302,
                headers={
                    "location": "https://storage.example.test/archive?signature=test",
                    "set-cookie": "session=private",
                },
            )
        return httpx.Response(200, content=b"archive")

    with HttpGitLab(
        "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
    ) as api:
        assert api.download("/projects/1/jobs/3/artifacts") == b"archive"
    assert "PRIVATE-TOKEN" in requests[0].headers
    assert "PRIVATE-TOKEN" not in requests[1].headers
    assert "JOB-TOKEN" not in requests[1].headers
    assert "cookie" not in requests[1].headers


@pytest.mark.parametrize(
    "target",
    [
        "http://storage.example.test/a",
        "https://user:secret@storage.example.test/a",
        "file:///etc/passwd",
    ],
)
def test_artifact_redirect_rejects_unsafe_targets(target: str) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": target})

    with (
        HttpGitLab(
            "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
        ) as api,
        pytest.raises(ValueError, match=r"URL|security"),
    ):
        api.download("/projects/1/jobs/3/artifacts")


class Chunked(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        """Fail if a consumer reads past the allowed response budget."""
        yield b"1234"
        yield b"5678"
        pytest.fail("The response was read after exceeding the configured limit")


@pytest.mark.parametrize("method", ["json", "artifact"])
def test_gitlab_streaming_limits_stop_before_reading_the_entire_body(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    monkeypatch.setattr(gitlab_api, "MAX_RESPONSE_BYTES", 5)

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Chunked())

    with HttpGitLab(
        "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
    ) as api:
        if method == "json":
            with pytest.raises(ValueError, match="size limit"):
                api.request("GET", "/projects")
        else:
            with pytest.raises(ValueError, match="size limit"):
                api.download("/projects/1/jobs/3/artifacts")


@pytest.mark.parametrize(
    "path",
    ["https://other.example.test", "//other.example.test/path", "/../outside", "/%2e%2e/outside"],
)
def test_gitlab_transport_rejects_paths_outside_the_configured_api(path: str) -> None:
    with (
        HttpGitLab("https://gitlab.example.test", "test-token") as api,
        pytest.raises(ValueError, match="configured API"),
    ):
        api.request("GET", path)


def test_gitlab_write_failure_is_not_retried() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503)

    with (
        HttpGitLab(
            "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
        ) as api,
        pytest.raises(GitLabError),
    ):
        api.request("POST", "/projects/1/merge_requests/2/notes", {"body": "note"})
    assert len(requests) == 1


@pytest.mark.parametrize("value", [None, True, "1", 0, -1])
def test_gitlab_ids_are_not_coerced(value: int | str | None) -> None:
    with pytest.raises((TypeError, ValueError)):
        integer(value, "identity")


def test_gitlab_external_value_validation() -> None:
    with pytest.raises(TypeError):
        object_value([], "response")
    with pytest.raises(TypeError):
        string(1, "name")
    with pytest.raises(ValueError, match="must not be empty"):
        string("", "name")
    with pytest.raises(ValueError, match="URL"):
        server_url("https://user:secret@example.test")


def test_transient_reads_are_retried_with_a_bounded_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter((503, 429, 200))
    waits: list[float] = []
    monkeypatch.setattr(gitlab_api.time, "sleep", waits.append)

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(statuses), json={"id": 1})

    with HttpGitLab(
        "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
    ) as api:
        assert api.request("GET", "/projects/1") == {"id": 1}
    assert waits == [1, 2]


def test_gitlab_pagination_is_bounded_and_requires_arrays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gitlab_api, "PAGE_SIZE", 1)
    monkeypatch.setattr(gitlab_api, "MAX_PAGES", 2)

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}])

    with (
        HttpGitLab(
            "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
        ) as api,
        pytest.raises(ValueError, match="page limit"),
    ):
        list(api.pages("/projects"))

    def invalid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 1})

    with (
        HttpGitLab(
            "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(invalid)
        ) as api,
        pytest.raises(TypeError, match="array"),
    ):
        list(api.pages("/projects"))


@pytest.mark.parametrize("status", [302, 403])
def test_external_artifact_redirects_are_bounded_and_fail_closed(status: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gitlab.example.test":
            return httpx.Response(302, headers={"location": "https://storage.example.test/archive"})
        return httpx.Response(status, headers={"location": "https://storage.example.test/archive"})

    with (
        HttpGitLab(
            "https://gitlab.example.test", "test-token", transport=httpx.MockTransport(respond)
        ) as api,
        pytest.raises((ValueError, GitLabError)),
    ):
        api.download("/projects/1/jobs/2/artifacts")
