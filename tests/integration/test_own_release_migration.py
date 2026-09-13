from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_cli.project import Project
from qg_github.compiler import pr_contract, project_graph
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue

pytestmark = pytest.mark.integration
ROOT = Path(__file__).parents[2]


def test_own_release_preserves_every_original_step_and_privilege(tmp_path: Path) -> None:
    (tmp_path / "qg.yaml").write_text((ROOT / "qg.yaml").read_text())
    project = Project.open(tmp_path)
    project.generate()
    assert project.validate().current
    original = yaml.safe_load((ROOT / "tests/fixtures/release-workflow-before.yml").read_text())
    generated = yaml.safe_load((tmp_path / ".github/workflows/release.yml").read_text())
    assert generated["on"] == original["on"]
    assert generated["permissions"] == original["permissions"]
    assert set(generated["jobs"]) == {*original["jobs"], "publish-gitlab"}
    for identifier, before in original["jobs"].items():
        after = generated["jobs"][identifier]
        needs = before.get("needs", [])
        expected_needs = [needs] if isinstance(needs, str) else needs
        if identifier == "release":
            expected_needs = [*expected_needs, "publish-gitlab"]
        assert after.get("needs", []) == expected_needs
        assert after["permissions"] == before.get("permissions", original["permissions"])
        assert after.get("environment") == before.get("environment")
        expected: list[dict[str, JsonValue]] = []
        for step in before["steps"]:
            item = dict(step)
            if "with" in item:
                item["with"] = {
                    key: str(value).lower() if isinstance(value, bool) else str(value)
                    for key, value in item["with"].items()
                }
            expected.append(item)
        assert after["steps"][: len(expected)] == expected
        offset = len(expected)
        if identifier == "build":
            upload = after["steps"][offset]
            assert upload["with"]["name"] == "quality-graph-gitlab"
            assert upload["with"]["path"] == "dist/quality_graph_gitlab-*"
            assert upload["with"]["if-no-files-found"] == "error"
            offset += 1
        collection = after["steps"][offset]
        assert collection["with"]["operation"] == "collect"
        assert collection["with"]["presentation"] == "release"
        assert collection["if"] == "always()"
        assert after["if"] == "startsWith(github.ref, 'refs/tags/')"


@pytest.mark.parametrize("event", ["pull-request", "push"])
def test_own_migration_does_not_weaken_or_reorder_quality_checks(event: str) -> None:
    before = Graph.from_yaml((ROOT / "tests/fixtures/quality-graph-before-release.yml").read_text())
    after = Graph.from_yaml((ROOT / "qg.yaml").read_text())
    projection = project_graph(after, event)
    additions = {"documentation", "mutation-full"}
    original = replace(
        projection, nodes=tuple(node for node in projection.nodes if node.id not in additions)
    )
    assert pr_contract(project_graph(before, event)) == pr_contract(original)


def test_own_documentation_and_full_mutation_are_graph_checks(tmp_path: Path) -> None:
    (tmp_path / "qg.yaml").write_text((ROOT / "qg.yaml").read_text())
    project = Project.open(tmp_path)
    project.generate()
    for filename in ("quality-graph.yml", "quality-graph-push.yml"):
        workflow = yaml.safe_load((tmp_path / ".github/workflows" / filename).read_text())
        job = workflow["jobs"]["documentation"]
        assert job["name"] == "Documentation"
        assert any(step.get("run") == "make site-build" for step in job["steps"])
        assert any(
            step.get("with", {}).get("name") == "documentation-site" for step in job["steps"]
        )
    main = yaml.safe_load((tmp_path / ".github/workflows/quality-graph-push.yml").read_text())
    assert any(
        step.get("run") == "make mutation" for step in main["jobs"]["mutation-full"]["steps"]
    )
    generated = {path.name for path in (tmp_path / ".github/workflows").glob("*.yml")}
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        if path.name in generated:
            continue
        workflow = yaml.safe_load(path.read_text())
        assert all("run" not in step for job in workflow["jobs"].values() for step in job["steps"])
    pages = yaml.safe_load((ROOT / ".github/workflows/pages.yml").read_text())
    assert pages["on"]["workflow_run"]["workflows"] == [main["name"]]
    deploy = pages["jobs"]["deploy"]
    assert "conclusion == 'success'" in deploy["if"]
    assert deploy["steps"][0]["with"]["run-id"] == "${{ github.event.workflow_run.id }}"
