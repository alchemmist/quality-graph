"""Supply project metadata to the documentation build."""

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from mkdocs.config.defaults import MkDocsConfig

if TYPE_CHECKING:
    from mkdocs.structure.files import Files
    from mkdocs.structure.pages import Page


class Adopter(TypedDict):
    """Describe one project shown on the documentation landing page."""

    name: str
    repository: str
    logo: str


class AdopterFile(TypedDict):
    """Describe the checked-in adopter catalog."""

    projects: list[Adopter]


def load_adopters(path: Path) -> list[Adopter]:
    """Load and validate the adopter catalog used by the site."""
    catalog = cast("AdopterFile", json.loads(path.read_text()))
    repositories: set[str] = set()
    for adopter in catalog["projects"]:
        if set(adopter) != {"name", "repository", "logo"} or not all(adopter.values()):
            message = f"Invalid adopter entry in {path}"
            raise ValueError(message)
        repository = adopter["repository"].lower()
        if repository in repositories:
            message = f"Duplicate adopter repository in {path}: {adopter['repository']}"
            raise ValueError(message)
        repositories.add(repository)
    return catalog["projects"]


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    """Expose project metadata to the site theme."""
    root = Path(__file__).parents[1]
    release = tomllib.loads((root / "docs-release.toml").read_text())
    config.extra["version"] = release["version"]
    config.extra["action_sha"] = release["action_sha"]
    config.extra["adopters"] = load_adopters(root / "docs/adopters.json")
    return config


def on_page_markdown(markdown: str, *, config: MkDocsConfig, **_kwargs: "Page | Files") -> str:
    """Render copyable pins from the same released toolchain as the footer."""
    return markdown.replace("QG_RELEASE_VERSION", config.extra["version"]).replace(
        "QG_RELEASE_SHA", config.extra["action_sha"]
    )
