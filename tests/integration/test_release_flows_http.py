from __future__ import annotations

import base64
from typing import TYPE_CHECKING, cast

import pytest
import yaml

from qg_cli.project import Project
from qg_github.artifacts import ArtifactExpectation, download_results
from qg_github.github import HttpGitHubPort
from qg_github.publication import publish_workflow_run
from qg_github.runtime import CollectionRequest, collect, publish_collection
from quality_graph_core.result import JsonValue, ResultStatus
from tests.integration.test_artifacts_http import archive, metadata
from tests.integration.test_github_lifecycle_http import workflow_event
from tests.test_flow_compiler import SOURCE
from tests.test_release_flows import RELEASE_SOURCE

if TYPE_CHECKING:
    from pathlib import Path

    from tests.integration.fake_github import FakeGitHubScenario

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("outcome", "status", "exit_code"),
    [
        ("success", ResultStatus.PASSED, 0),
        ("failure", ResultStatus.FAILED, 1),
    ],
)
def test_release_results_round_trip_through_http_without_pr_side_effects(
    fake_github: FakeGitHubScenario,
    tmp_path: Path,
    outcome: str,
    status: ResultStatus,
    exit_code: int,
) -> None:
    (tmp_path / "qg.yaml").write_text(RELEASE_SOURCE)
    project = Project.open(tmp_path)
    compiled = project.generate()
    assert project.validate().current
    workflow = yaml.safe_load((tmp_path / ".github/workflows/release.yml").read_text())
    inputs = next(
        step["with"]
        for step in workflow["jobs"]["build"]["steps"]
        if step.get("id") == "quality-result"
    )
    environment = {f"QG_{key.upper().replace('-', '_')}": value for key, value in inputs.items()}
    environment.update(
        {
            "QG_COMMAND_OUTCOME": outcome,
            "GITHUB_WORKSPACE": str(tmp_path),
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
            "GITHUB_OUTPUT": str(tmp_path / "output"),
            "GITHUB_REPOSITORY": "owner/repository",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "10",
            "GITHUB_RUN_ATTEMPT": "1",
        }
    )
    request = CollectionRequest.from_environment(environment, {"ref": "refs/tags/v1.0.0"})
    result = collect(request)
    assert publish_collection(request, result) == exit_code
    assert result.status is status
    assert result.controls == ()
    assert result.provenance.pull_request is None
    content = archive(result)
    fake_github.reset(
        {
            "run_artifacts": {"10": [metadata(1, "quality-result-build-1", content)]},
            "downloads": {"1": base64.b64encode(content).decode()},
        }
    )
    port = HttpGitHubPort("owner/repository", "token", base_url=fake_github.base_url)
    loaded = download_results(
        port,
        ArtifactExpectation(
            "owner/repository",
            None,
            "a" * 40,
            10,
            compiled.graph_digest,
            frozenset({"build"}),
            "release",
            {"build": "build"},
        ),
    )
    assert loaded == {"build": result}
    event = cast("dict[str, JsonValue]", workflow_event())
    cast("dict[str, JsonValue]", event["workflow_run"])["event"] = "push"
    assert not publish_workflow_run(port, event).published
    observed = fake_github.snapshot()
    assert observed["comments"] == observed["checks"] == observed["labels"] == []
    assert all(
        item["method"] == "GET" for item in cast("list[dict[str, JsonValue]]", observed["requests"])
    )


def test_disabling_release_retires_only_the_compiler_owned_workflow(tmp_path: Path) -> None:
    source = tmp_path / "qg.yaml"
    source.write_text(RELEASE_SOURCE)
    Project.open(tmp_path).generate()
    release = tmp_path / ".github/workflows/release.yml"
    assert release.exists()
    source.write_text(SOURCE)
    project = Project.open(tmp_path)
    assert "retired generated file: .github/workflows/release.yml" in project.validate().problems
    project.generate()
    assert not release.exists()
    release.write_text("name: Independently managed release\n")
    project.generate()
    assert release.read_text() == "name: Independently managed release\n"
    assert project.validate().current


@pytest.mark.parametrize("mode", ["github-actions", "design-only"])
def test_generated_release_cannot_write_through_an_escaping_parent(
    tmp_path: Path, mode: str
) -> None:
    root = tmp_path / "project"
    external = tmp_path / "external"
    external.mkdir()
    (root / ".github").mkdir(parents=True)
    (root / ".github/workflows").symlink_to(external, target_is_directory=True)
    (root / "qg.yaml").write_text(
        RELEASE_SOURCE.replace("execution: github-actions", f"execution: {mode}")
    )
    with pytest.raises(ValueError, match="outside the project root"):
        Project.open(root).generate()
    assert list(external.iterdir()) == []


def test_generated_release_refuses_a_final_component_symlink(tmp_path: Path) -> None:
    (tmp_path / "qg.yaml").write_text(RELEASE_SOURCE)
    (tmp_path / ".github/workflows").mkdir(parents=True)
    target = tmp_path / "user-file.yml"
    target.write_text("preserve user data\n")
    (tmp_path / ".github/workflows/release.yml").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        Project.open(tmp_path).generate()
    assert target.read_text() == "preserve user data\n"
