from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_cli.project import Project
from qg_github.compiler import pr_contract, project_graph
from quality_graph_core.graph import AdapterKind, Graph

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
    assert set(generated["jobs"]) == set(original["jobs"])
    for identifier, before in original["jobs"].items():
        after = generated["jobs"][identifier]
        needs = before.get("needs", [])
        assert after.get("needs", []) == ([needs] if isinstance(needs, str) else needs)
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
        collection = after["steps"][len(expected)]
        assert collection["with"]["operation"] == "collect"
        assert collection["with"]["presentation"] == "release"
        assert collection["if"] == "always()"
        assert after["if"] == "startsWith(github.ref, 'refs/tags/')"


@pytest.mark.parametrize("event", ["pull-request", "push"])
def test_own_migration_does_not_weaken_or_reorder_quality_checks(event: str) -> None:
    before = Graph.from_yaml((ROOT / "tests/fixtures/quality-graph-before-release.yml").read_text())
    after = Graph.from_yaml((ROOT / "qg.yaml").read_text())
    original = project_graph(before, event)
    current = project_graph(after, event)
    normalized = []
    for old, new in zip(original.nodes, current.nodes, strict=True):
        assert new.id == old.id
        assert new.result.kind is AdapterKind.NATIVE
        assert new.result.path == f"reports/{new.id}.json"
        command = (
            "make python-object-annotations" if new.id == "object-annotations" else old.step.run
        )
        expected = (
            command
            if new.id in {"test-fast", "test-medium"}
            else (
                "uv run --locked --all-packages python scripts/check_report.py "
                f"--output reports/{new.id}.json -- {command}"
            )
        )
        assert new.step == replace(old.step, run=expected)
        normalized.append(replace(new, step=old.step, result=old.result))
    assert pr_contract(original) == pr_contract(replace(current, nodes=tuple(normalized)))
