"""Supply project metadata to the documentation build."""

import tomllib
from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig


def on_config(config: MkDocsConfig) -> MkDocsConfig:
    """Expose the CLI package version to the site theme."""
    project_file = Path(__file__).parents[1] / "apps/qg/pyproject.toml"
    project = tomllib.loads(project_file.read_text())
    config.extra["version"] = project["project"]["version"]
    return config
