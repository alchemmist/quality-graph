from pathlib import Path

import pytest

from quality_graph.project import Project


@pytest.mark.parametrize("ecosystem", ["python", "typescript", "go"])
def test_example_repository_uses_public_graph_and_generated_interfaces(ecosystem: str) -> None:
    root = Path(__file__).parents[1] / "examples" / ecosystem
    project = Project.open(root)

    assert project.graph.nodes
    assert project.validate().current
    assert {str(item.path) for item in project.render().files} == {
        ".github/workflows/quality-graph.yml",
        ".github/workflows/quality-graph-publish.yml",
        ".quality-graph/manifest.json",
    }
