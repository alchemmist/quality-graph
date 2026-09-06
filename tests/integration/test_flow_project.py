from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from qg_cli.project import Project
from qg_github.compiler import GENERATED_HEADER
from tests.test_flow_compiler import SOURCE
from tests.test_graph import GRAPH

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration


def test_migration_removes_retired_publisher_and_reports_stale_files(tmp_path: Path) -> None:
    declaration = tmp_path / "qg.yaml"
    declaration.write_text(GRAPH)
    Project.open(tmp_path).generate()
    publisher = tmp_path / ".github/workflows/quality-graph-publish.yml"
    assert publisher.exists()
    declaration.write_text(SOURCE.replace("presentation: github-pr", "presentation: none"))
    project = Project.open(tmp_path)
    assert any("retired generated file" in item for item in project.validate().problems)

    project.generate()

    assert not publisher.exists()
    assert project.validate().current
    assert "quality-graph-publish.yml" not in (tmp_path / ".prettierignore").read_text()
    project.generate()
    assert project.validate().current


def test_migration_preserves_unmanaged_workflow(tmp_path: Path) -> None:
    declaration = tmp_path / "qg.yaml"
    declaration.write_text(GRAPH)
    Project.open(tmp_path).generate()
    publisher = tmp_path / ".github/workflows/quality-graph-publish.yml"
    publisher.write_text("name: User maintained workflow\n")
    declaration.write_text(SOURCE.replace("presentation: github-pr", "presentation: none"))

    with pytest.raises(ValueError, match="unmanaged"):
        Project.open(tmp_path).generate()

    assert publisher.read_text() == "name: User maintained workflow\n"


def test_retired_workflow_cannot_escape_through_a_parent_symlink(tmp_path: Path) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external"
    external.mkdir()
    (root / ".github").mkdir(parents=True)
    (root / ".github/workflows").symlink_to(external, target_is_directory=True)
    content = GENERATED_HEADER + "name: Existing generated workflow\n"
    publisher = external / "quality-graph-publish.yml"
    publisher.write_text(content)
    (root / "qg.yaml").write_text(SOURCE.replace("presentation: github-pr", "presentation: none"))
    project = Project.open(root)

    with pytest.raises(ValueError, match="outside the project root"):
        project.generate()
    with pytest.raises(ValueError, match="outside the project root"):
        project.validate()

    assert publisher.read_text() == content
    assert not (external / "quality-graph.yml").exists()


def test_dangling_retired_symlink_is_reported_and_preserved(tmp_path: Path) -> None:
    (tmp_path / "qg.yaml").write_text(
        SOURCE.replace("presentation: github-pr", "presentation: none")
    )
    project = Project.open(tmp_path)
    project.generate()
    publisher = tmp_path / ".github/workflows/quality-graph-publish.yml"
    publisher.symlink_to(tmp_path / "missing.yml")

    assert (
        "retired generated file: .github/workflows/quality-graph-publish.yml"
        in project.validate().problems
    )
    with pytest.raises(ValueError, match="unmanaged"):
        project.generate()

    assert publisher.is_symlink()
    assert not (tmp_path / "missing.yml").exists()
