"""Exercise adopter discovery without live GitHub requests."""

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
import qg_adopter_discovery.cli as cli
from qg_adopter_discovery.cli import (
    API_HOST,
    API_ORIGIN,
    SEARCHES,
    Candidate,
    GitHubClient,
    RepositoryResponse,
    SearchItem,
    api_path,
    discover,
    github_connection,
    https_proxy,
    listed_repositories,
    render_table,
)


class FakeGitHubClient:
    """Provide deterministic search and repository responses."""

    def search_code(self, query: str, max_pages: int) -> tuple[SearchItem, ...]:
        """Return deterministic matches for both strong signals."""
        assert max_pages == 1
        if query == SEARCHES["action"]:
            return (
                {"repository": {"full_name": "owner/popular"}},
                {"repository": {"full_name": "owner/new"}},
            )
        return ({"repository": {"full_name": "owner/popular"}},)

    def repository(self, full_name: str) -> RepositoryResponse:
        """Return deterministic popularity metadata."""
        stars = {"owner/popular": 12, "owner/new": 4}[full_name]
        return {
            "archived": False,
            "description": f"Description for {full_name}",
            "fork": False,
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "stargazers_count": stars,
        }


class FakeResponse:
    """Provide one scripted HTTP response."""

    def __init__(self, status: int, body: bytes = b"", location: str | None = None) -> None:
        """Store response status, body, and optional redirect location."""
        self.status = status
        self.reason = "scripted"
        self._body = body
        self._location = location

    def getheader(self, name: str) -> str | None:
        """Return the scripted Location header."""
        return self._location if name == "Location" else None

    def read(self) -> bytes:
        """Return the scripted response body."""
        return self._body


class FakeHttpsConnection:
    """Record connection and tunnel parameters without network access."""

    def __init__(
        self,
        host: str,
        port: int | None,
        timeout: int,
        response: FakeResponse | None = None,
    ) -> None:
        """Store connection arguments and an optional response."""
        self.host = host
        self.port = port
        self.timeout = timeout
        self.response = response
        self.tunnel: tuple[str, int] | None = None

    def set_tunnel(self, host: str, port: int) -> None:
        """Record the requested CONNECT destination."""
        self.tunnel = (host, port)

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        """Accept one scripted request."""
        del method, path, headers

    def getresponse(self) -> FakeResponse:
        """Return the scripted response."""
        if self.response is None:
            message = "no scripted response"
            raise RuntimeError(message)
        return self.response

    def close(self) -> None:
        """Close the fake connection."""


def test_discovery_deduplicates_and_ranks_strong_matches() -> None:
    """Combine strong signals and rank unique repositories by stars."""
    candidates = discover(FakeGitHubClient(), frozenset({"owner/popular"}), max_pages=1)

    assert [candidate.full_name for candidate in candidates] == ["owner/popular", "owner/new"]
    assert candidates[0].evidence == ("action", "generated workflow")
    assert candidates[0].listed
    assert not candidates[1].listed


def test_package_mention_is_not_a_search_signal() -> None:
    """Do not treat a package-name mention as adopter evidence."""
    assert "package" not in SEARCHES
    assert all("quality-graph-cli" not in query for query in SEARCHES.values())


def test_render_table_uses_aligned_ascii_grid() -> None:
    """Render candidates in an aligned terminal-friendly table."""
    candidate = Candidate(
        full_name="owner/project",
        url="https://github.com/owner/project",
        stars=1_234,
        description="Quality checks",
        evidence=("action",),
        listed=False,
    )

    table = render_table((candidate,))

    assert table.startswith("+")
    assert "+=========+" in table
    assert "| owner/project" in table
    assert "1,234" in table
    assert "new" in table


def test_listed_repositories_normalizes_catalog_names(tmp_path: Path) -> None:
    """Compare discovered repositories case-insensitively."""
    path = tmp_path / "adopters.json"
    path.write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "name": "Project",
                        "repository": "Owner/Project",
                        "logo": "assets/project.svg",
                    }
                ]
            }
        )
    )

    assert listed_repositories(path) == frozenset({"owner/project"})


@pytest.mark.parametrize(
    ("proxies", "bypass", "expected"),
    [
        ({}, False, None),
        ({"https": "http://proxy.example:8080"}, False, "http://proxy.example:8080"),
        ({"https": "http://proxy.example:8080"}, True, None),
    ],
)
def test_https_proxy_selection(
    monkeypatch: pytest.MonkeyPatch,
    proxies: dict[str, str],
    bypass: bool,
    expected: str | None,
) -> None:
    """Honor direct, proxied, and no_proxy connection paths."""
    monkeypatch.setattr(cli, "getproxies", lambda: proxies)
    monkeypatch.setattr(cli, "proxy_bypass", lambda _host: bypass)

    assert https_proxy() == expected


def test_api_path_accepts_same_origin_https_redirect() -> None:
    """Follow a moved GitHub repository without changing API origin."""
    assert api_path(f"{API_ORIGIN}/repositories/1?tracked=true") == (
        "/repositories/1?tracked=true"
    )


def test_api_path_rejects_cross_origin_redirect() -> None:
    """Prevent a GitHub token from crossing an API origin boundary."""
    with pytest.raises(RuntimeError, match="refusing GitHub API redirect"):
        api_path("https://example.com/repositories/1")


def test_github_client_follows_and_decodes_repository_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode the canonical repository after a bounded same-origin redirect."""
    repository = {
        "archived": False,
        "description": "Moved repository",
        "fork": False,
        "full_name": "owner/new",
        "html_url": "https://github.com/owner/new",
        "stargazers_count": 10,
    }
    connections = [
        FakeHttpsConnection(
            API_HOST,
            None,
            30,
            FakeResponse(301, location=f"{API_ORIGIN}/repositories/1"),
        ),
        FakeHttpsConnection(
            API_HOST,
            None,
            30,
            FakeResponse(200, json.dumps(repository).encode()),
        ),
    ]
    monkeypatch.setattr(cli, "https_proxy", lambda: None)
    monkeypatch.setattr(cli, "github_connection", lambda _proxy: connections.pop(0))

    assert GitHubClient("token").repository("owner/old") == repository


def test_github_connection_uses_connect_tunnel_for_proxy(
) -> None:
    """Tunnel GitHub authorization through the configured HTTPS proxy."""
    with patch("qg_adopter_discovery.cli.http.client.HTTPSConnection") as factory:
        connection = github_connection("http://proxy.example:8080")

    assert connection is factory.return_value
    factory.assert_called_once_with("proxy.example", 8080, timeout=30)
    mock_connection = cast("MagicMock", factory.return_value)
    mock_connection.set_tunnel.assert_called_once_with(API_HOST, 443)
