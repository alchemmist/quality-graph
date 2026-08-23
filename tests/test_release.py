import re
import tomllib
from pathlib import Path

import yaml

VERSION = "0.1.2"
PROJECT_NAMES = {
    "quality-graph-core",
    "quality-graph-github",
    "quality-graph-python",
    "quality-graph-cli",
}
PROJECT_FILES = (
    Path("packages/core/pyproject.toml"),
    Path("packages/github/pyproject.toml"),
    Path("packages/python/pyproject.toml"),
    Path("apps/qg/pyproject.toml"),
)
PUBLISH_ENVIRONMENTS = {
    "publish-core": "pypi",
    "publish-python": "pypi-python",
    "publish-github": "pypi-github",
    "publish-cli": "pypi-cli",
}
PINNED_ACTION = re.compile(r"^[^@]+@[0-9a-f]{40}$")


def test_workspace_releases_one_exact_version() -> None:
    projects = [tomllib.loads(path.read_text()) for path in PROJECT_FILES]

    assert {project["project"]["name"] for project in projects} == PROJECT_NAMES
    assert {project["project"]["version"] for project in projects} == {VERSION}
    assert projects[1]["project"]["dependencies"][-1] == f"quality-graph-core=={VERSION}"
    assert projects[3]["project"]["dependencies"] == [f"quality-graph-core=={VERSION}"]


def test_release_workflow_is_tag_bound_and_least_privilege() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text())
    jobs = workflow["jobs"]

    assert workflow["on"]["push"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in jobs["build"]
    assert {step.get("run") for step in jobs["build"]["steps"]} >= {'make check BASE="$GITHUB_SHA"'}
    for name, environment in PUBLISH_ENVIRONMENTS.items():
        assert jobs[name]["environment"]["name"] == environment
        assert jobs[name]["permissions"] == {"id-token": "write"}
    assert jobs["release"]["permissions"] == {"contents": "write"}
    assert {step.get("run") for step in jobs["release"]["steps"]} >= {
        'gh release create "$GITHUB_REF_NAME" dist/* --generate-notes '
        '--verify-tag --repo "$GITHUB_REPOSITORY"'
    }


def test_release_workflow_pins_every_external_action() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/release.yml").read_text())
    actions = [
        step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step
    ]

    assert actions
    assert all(PINNED_ACTION.fullmatch(action) for action in actions)
