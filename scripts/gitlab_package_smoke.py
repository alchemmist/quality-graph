"""Validate an isolated installation containing only CLI, core and GitLab provider."""

from __future__ import annotations

from importlib.metadata import distributions
from pathlib import Path
from tempfile import TemporaryDirectory

from qg_cli.project import Project
from qg_cli.providers import load_provider


def main() -> None:
    """Exercise the public onboarding interface without source-tree imports."""
    installed = {distribution.metadata["Name"] for distribution in distributions()}
    if "quality-graph-github" in installed:
        message = "GitLab clean installation unexpectedly includes the GitHub provider"
        raise RuntimeError(message)
    if load_provider("gitlab").name != "gitlab":
        message = "GitLab provider discovery failed"
        raise RuntimeError(message)
    with TemporaryDirectory(prefix="qg-gitlab-package-") as directory:
        root = Path(directory)
        project = Project.initialize_provider(root, "gitlab")
        project.generate()
        if not project.validate().current or not (root / ".gitlab-ci.yml").is_file():
            message = "GitLab package initialization or generation failed"
            raise RuntimeError(message)


if __name__ == "__main__":
    main()
