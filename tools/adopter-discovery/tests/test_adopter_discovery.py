"""Exercise adopter discovery without live GitHub requests."""

import json
from pathlib import Path

import pytest
from qg_adopter_discovery.cli import (
    SEARCHES,
    Candidate,
    GitHubClient,
    RepositoryResponse,
    SearchItem,
    discover,
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


class FailingConnection:
    """Model a transport failure without performing a network request."""

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        """Raise the same error family as a failed HTTPS connection."""
        del method, path, headers
        message = "offline"
        raise OSError(message)

    def close(self) -> None:
        """Allow the client to close a failed connection safely."""


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


def test_github_client_wraps_transport_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report connection failures without leaking a raw transport traceback."""
    monkeypatch.setattr(
        "qg_adopter_discovery.cli.http.client.HTTPSConnection",
        lambda *_args, **_kwargs: FailingConnection(),
    )

    with pytest.raises(RuntimeError, match="GitHub API request failed: offline"):
        GitHubClient("token").search_code(SEARCHES["action"], 1)
