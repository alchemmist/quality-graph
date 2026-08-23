import json

import httpx
import pytest

from qg_github.github import (
    GITHUB_PAGE_SIZE,
    GitHubError,
    HttpGitHubPort,
    MemoryGitHubPort,
    paged,
)


def test_http_port_scopes_requests_and_hides_credentials() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    port = HttpGitHubPort(
        "owner/repository",
        "secret",
        base_url="https://github.test",
        client=client,
    )

    assert port.request("POST", "/issues", {"title": "Issue"}) == {"ok": True}
    assert observed[0].url == "https://github.test/repos/owner/repository/issues"
    assert observed[0].headers["authorization"] == "Bearer secret"
    assert json.loads(observed[0].content) == {"title": "Issue"}


def test_http_port_handles_empty_missing_error_and_invalid_responses() -> None:
    responses = iter(
        [
            httpx.Response(204),
            httpx.Response(404),
            httpx.Response(422),
            httpx.Response(200, text="invalid"),
        ]
    )
    port = HttpGitHubPort(
        "owner/repository",
        "token",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))),
    )

    assert port.request("POST", "/empty") is None
    assert port.request("GET", "/missing") is None
    with pytest.raises(GitHubError, match="HTTP 422") as captured:
        port.request("POST", "/invalid")
    assert captured.value.status_code == 422
    with pytest.raises(RuntimeError, match="invalid JSON"):
        port.request("GET", "/broken")


def test_http_port_masks_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = "token=secret"
        raise httpx.ConnectError(message, request=request)

    port = HttpGitHubPort(
        "owner/repository",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match="ConnectError") as captured:
        port.request("GET", "/failure")
    assert "secret" not in str(captured.value)


def test_http_port_reads_environment_and_user_path() -> None:
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(str(request.url))
        return httpx.Response(200, json={"login": "bot"})

    port = HttpGitHubPort.from_environment(
        {
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_TOKEN": "token",
            "GITHUB_API_URL": "https://github.test",
        }
    )
    port.client = httpx.Client(transport=httpx.MockTransport(handler))

    assert port.request("GET", "/user") == {"login": "bot"}
    assert observed == ["https://github.test/user"]


def test_http_port_downloads_binary_content_and_handles_failures() -> None:
    responses = iter([httpx.Response(200, content=b"archive"), httpx.Response(403)])
    port = HttpGitHubPort(
        "owner/repository",
        "token",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: next(responses))),
    )

    assert port.download("/actions/artifacts/1/zip") == b"archive"
    with pytest.raises(GitHubError, match="HTTP 403"):
        port.download("/actions/artifacts/2/zip")


def test_http_port_masks_download_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = "token=secret"
        raise httpx.ConnectError(message, request=request)

    port = HttpGitHubPort(
        "owner/repository",
        "secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match="ConnectError") as captured:
        port.download("/failure")
    assert "secret" not in str(captured.value)


def test_memory_port_records_and_sequences_exact_requests() -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", "/state", {"value": 1}, {"value": 2})

    assert port.request("GET", "/state") == {"value": 1}
    assert port.request("GET", "/state") == {"value": 2}
    assert port.request("GET", "/state") == {"value": 2}
    assert port.requests == [
        ("GET", "/state", None),
        ("GET", "/state", None),
        ("GET", "/state", None),
    ]
    with pytest.raises(AssertionError, match="Unexpected"):
        port.request("POST", "/state")

    port.downloads["/archive"] = b"content"
    assert port.download("/archive") == b"content"
    assert port.downloaded == ["/archive"]
    with pytest.raises(AssertionError, match="Unexpected"):
        port.download("/missing")


def test_pagination_reads_every_page_and_preserves_query_strings() -> None:
    port = MemoryGitHubPort()
    first = [{"id": index} for index in range(GITHUB_PAGE_SIZE)]
    port.enqueue("GET", "/items?state=open&per_page=100&page=1", first)
    port.enqueue("GET", "/items?state=open&per_page=100&page=2", [{"id": 100}])

    result = paged(port, "/items?state=open")

    assert len(result) == 101
    assert result[-1] == {"id": 100}


@pytest.mark.parametrize("response", [{"not": "array"}, ["not-object"]])
def test_pagination_narrows_responses(response: object) -> None:
    port = MemoryGitHubPort()
    port.enqueue("GET", "/items?per_page=100&page=1", response)
    with pytest.raises(TypeError):
        paged(port, "/items")
