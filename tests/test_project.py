from pathlib import Path

import pytest

from qg_cli.project import Project
from qg_github.compiler import EXECUTION_WORKFLOW

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


def test_generate_manages_prettier_ignore_without_replacing_user_rules(tmp_path: Path) -> None:
    prettier_ignore = tmp_path / ".prettierignore"
    prettier_ignore.write_text("dist\n")
    project = Project.initialize(tmp_path, RUNTIME)

    project.generate()
    first = prettier_ignore.read_text()
    project.generate()

    assert prettier_ignore.read_text() == first
    assert first == (
        "dist\n\n"
        "# Quality Graph generated files (managed by qg)\n"
        ".github/workflows/quality-graph.yml\n"
        ".github/workflows/quality-graph-publish.yml\n"
        ".quality-graph/manifest.json\n"
        "# End Quality Graph generated files\n"
    )


def test_generate_replaces_its_existing_prettier_block_in_place(tmp_path: Path) -> None:
    prettier_ignore = tmp_path / ".prettierignore"
    prettier_ignore.write_text(
        "before\n"
        "# Quality Graph generated files (managed by qg)\n"
        "obsolete.json\n"
        "# End Quality Graph generated files\n"
        "after\n"
    )

    Project.initialize(tmp_path, RUNTIME).generate()

    assert prettier_ignore.read_text().splitlines() == [
        "before",
        "# Quality Graph generated files (managed by qg)",
        ".github/workflows/quality-graph.yml",
        ".github/workflows/quality-graph-publish.yml",
        ".quality-graph/manifest.json",
        "# End Quality Graph generated files",
        "after",
    ]


def test_generate_separates_unterminated_prettier_rules(tmp_path: Path) -> None:
    prettier_ignore = tmp_path / ".prettierignore"
    prettier_ignore.write_text("dist")

    Project.initialize(tmp_path, RUNTIME).generate()

    assert prettier_ignore.read_text().startswith(
        "dist\n\n# Quality Graph generated files (managed by qg)\n"
    )


def test_generate_rejects_malformed_prettier_block_before_writing_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / ".prettierignore").write_text("# Quality Graph generated files (managed by qg)\n")
    project = Project.initialize(tmp_path, RUNTIME)

    with pytest.raises(ValueError, match="Malformed Quality Graph block"):
        project.generate()

    assert not (tmp_path / EXECUTION_WORKFLOW).exists()


def test_project_refuses_missing_or_existing_declaration(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        Project.open(tmp_path)
    Project.initialize(tmp_path, RUNTIME)
    with pytest.raises(FileExistsError, match="Refusing to replace"):
        Project.initialize(tmp_path, RUNTIME)

    replaced = Project.initialize(tmp_path, RUNTIME, force=True)
    assert replaced.graph.provider.values["runtime"] == {"action": RUNTIME}


def test_project_does_not_write_when_default_provider_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_name: str) -> object:
        message = "provider missing"
        raise ValueError(message)

    monkeypatch.setattr("qg_cli.project.load_provider", missing)

    with pytest.raises(ValueError, match="provider missing"):
        Project.initialize(tmp_path, RUNTIME)

    assert not (tmp_path / "quality-graph.yml").exists()
