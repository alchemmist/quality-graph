from pathlib import Path

import pytest

from quality_graph.compiler import EXECUTION_WORKFLOW
from quality_graph.project import Project

RUNTIME = "alchemmist/quality-graph@" + "a" * 40


def test_project_initializes_generates_and_validates_repository(tmp_path: Path) -> None:
    project = Project.initialize(tmp_path, RUNTIME)

    assert project.graph.nodes[0].id == "quality"
    assert project.validate().current is False
    generated = project.generate()
    assert len(generated.files) == 3
    assert project.validate().current is True

    workflow = tmp_path / EXECUTION_WORKFLOW
    workflow.write_text("stale")
    report = project.validate()
    assert report.current is False
    assert report.problems == (f"stale generated file: {EXECUTION_WORKFLOW}",)


def test_project_reports_every_missing_generated_file(tmp_path: Path) -> None:
    project = Project.initialize(tmp_path, RUNTIME, preset="internal")

    assert project.graph.profiles[0].runner == "self-hosted"
    assert project.validate().problems == (
        "missing generated file: .github/workflows/quality-graph.yml",
        "missing generated file: .github/workflows/quality-graph-publish.yml",
        "missing generated file: .quality-graph/manifest.json",
    )


def test_project_refuses_missing_or_existing_declaration(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        Project.open(tmp_path)
    Project.initialize(tmp_path, RUNTIME)
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        Project.initialize(tmp_path, RUNTIME)

    replaced = Project.initialize(tmp_path, RUNTIME, force=True)
    assert replaced.graph.runtime.action == RUNTIME
