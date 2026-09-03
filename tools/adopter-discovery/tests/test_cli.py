import json
from pathlib import Path

from qg_adopter_discovery.cli import (
    SEARCHES,
    Candidate,
    RepositoryResponse,
    SearchItem,
    discover,
    listed_repositories,
    render_table,
)


class FakeGitHubClient:
    def search_code(self, query: str, max_pages: int) -> tuple[SearchItem, ...]:
        assert max_pages == 1
        if query == SEARCHES["action"]:
            return (
                {"repository": {"full_name": "owner/popular"}},
                {"repository": {"full_name": "owner/new"}},
            )
        return ({"repository": {"full_name": "owner/popular"}},)

    def repository(self, full_name: str) -> RepositoryResponse:
        stars = {"owner/popular": 12, "owner/new": 4}[full_name]
        return {
            "archived": False,
            "description": f"Description for {full_name}",
            "fork": False,
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "stargazers_count": stars,
        }


def test_discovery_deduplicates_and_ranks_strong_matches() -> None:
    candidates = discover(FakeGitHubClient(), frozenset({"owner/popular"}), max_pages=1)

    assert [candidate.full_name for candidate in candidates] == ["owner/popular", "owner/new"]
    assert candidates[0].evidence == ("action", "generated workflow")
    assert candidates[0].listed
    assert not candidates[1].listed


def test_package_mention_is_not_a_search_signal() -> None:
    assert "package" not in SEARCHES
    assert all("quality-graph-cli" not in query for query in SEARCHES.values())


def test_render_table_uses_aligned_ascii_grid() -> None:
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
