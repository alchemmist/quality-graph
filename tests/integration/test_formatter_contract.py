import subprocess
from pathlib import Path
from shutil import which

import pytest

from qg_cli.project import Project

RUNTIME = "alchemmist/quality-graph@" + "a" * 40


@pytest.mark.integration
def test_prettier_can_format_fresh_project_without_staling_artifacts(tmp_path: Path) -> None:
    project = Project.initialize(tmp_path, RUNTIME)
    project.generate()
    npx = which("npx")
    assert npx is not None

    subprocess.run(
        [npx, "--yes", "prettier@3.6.2", "--write", "."],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert project.validate().current
