from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import yaml

from qg_cli.cli import main
from qg_cli.project import Project
from qg_gitlab import provider
from quality_graph_core.result import GitLabProvenance, Result
from quality_graph_core.schema import gitlab_result_schema_value, result_schema_json

if TYPE_CHECKING:
    from pathlib import Path


def test_cli_emits_native_gitlab_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "result",
                "emit",
                "--provider",
                "gitlab",
                "--node-id",
                "test",
                "--title",
                "Tests",
                "--status",
                "passed",
                "--head-sha",
                "a" * 40,
                "--graph-digest",
                "b" * 64,
                "--server-url",
                "https://gitlab.example.test",
                "--project-id",
                "1",
                "--pipeline-id",
                "2",
                "--job-id",
                "3",
            ]
        )
        == 0
    )
    result = Result.from_json(capsys.readouterr().out)
    assert isinstance(result.provenance, GitLabProvenance)
    assert result.schema_version == 1
    assert result.provenance.job_id == 3


def test_cli_reports_missing_provider_specific_result_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "result",
                "emit",
                "--provider",
                "gitlab",
                "--node-id",
                "test",
                "--title",
                "Tests",
                "--status",
                "passed",
                "--head-sha",
                "a" * 40,
                "--graph-digest",
                "b" * 64,
            ]
        )
    assert "--project-id" in capsys.readouterr().err


def test_cli_publishes_both_result_schema_versions(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["result", "schema", "--schema-version", "1"]) == 0
    assert json.loads(capsys.readouterr().out) == gitlab_result_schema_value()
    assert json.loads(result_schema_json())["properties"]["schemaVersion"] == {"const": 0}
    with pytest.raises(ValueError, match="unsupported"):
        result_schema_json(2)


def test_gitlab_unmanaged_ci_is_preserved(tmp_path: Path) -> None:
    Project.initialize_provider(tmp_path, "gitlab")
    declaration = yaml.safe_load((tmp_path / "qg.yaml").read_text())
    declaration["provider"]["configuration"]["publisher-user-id"] = 42
    (tmp_path / "qg.yaml").write_text(yaml.safe_dump(declaration))
    project = Project.open(tmp_path)
    ci = tmp_path / ".gitlab-ci.yml"
    ci.write_text("existing: {script: 'echo keep'}\n")
    with pytest.raises(FileExistsError, match="unmanaged GitLab"):
        project.generate()
    assert ci.read_text() == "existing: {script: 'echo keep'}\n"
    source = yaml.safe_load((tmp_path / "qg.yaml").read_text())
    source["provider"]["configuration"]["publisher-user-id"] = 42
    source["provider"]["configuration"]["ci-path"] = ".qg/gitlab-ci.yml"
    (tmp_path / "qg.yaml").write_text(yaml.safe_dump(source))
    project = Project.open(tmp_path)
    project.generate()
    assert project.validate().current
    assert ci.read_text() == "existing: {script: 'echo keep'}\n"


def test_provider_initialization_reports_unsupported_capabilities(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="does not provide initialization"):
        Project.initialize_provider(tmp_path, "github")
    with pytest.raises(ValueError, match="unsupported GitLab preset"):
        provider.starter_configuration("main", "unknown")
