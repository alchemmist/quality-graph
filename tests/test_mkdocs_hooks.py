import json
from pathlib import Path

import pytest
from scripts.mkdocs_hooks import load_adopters


def test_site_loads_adopter_catalog(tmp_path: Path) -> None:
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

    assert load_adopters(path)[0]["name"] == "Project"


def test_site_catalog_rejects_duplicate_repositories(tmp_path: Path) -> None:
    path = tmp_path / "adopters.json"
    project = {"name": "Project", "repository": "owner/project", "logo": "logo.svg"}
    path.write_text(json.dumps({"projects": [project, project]}))

    with pytest.raises(ValueError, match="Duplicate adopter repository"):
        load_adopters(path)
