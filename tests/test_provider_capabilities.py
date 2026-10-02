from pathlib import Path, PurePosixPath

import pytest

from qg_cli.project import Project
from qg_github.compiler import CAPABILITIES as GITHUB_CAPABILITIES
from qg_gitlab.compiler import CAPABILITIES as GITLAB_CAPABILITIES
from quality_graph_core.graph import Graph
from quality_graph_core.provider import (
    GeneratedFile,
    GeneratedProject,
    ProfileDefaults,
    ProviderCapabilities,
)


def test_custom_provider_owns_capabilities_and_profile_defaults() -> None:
    graph = Graph.from_yaml(
        "version: 0\nprovider: {name: custom}\nprofiles: {default: {}}\n"
        "operations: {check: {run: check}}\n"
        "flows: {nightly: {trigger: schedule, presentation: custom-dashboard, nodes: {check: {}}}}"
    )
    capabilities = ProviderCapabilities(
        frozenset({"schedule"}),
        {"custom-dashboard": frozenset({"schedule"})},
        frozenset({"design-only"}),
        ProfileDefaults("custom-worker", {"repository": "read"}),
    )
    capabilities.validate(graph)
    profile = graph.expanded_profiles(capabilities.profile_defaults)["default"]
    assert profile.runner == "custom-worker"
    assert profile.permissions == {"repository": "read"}
    for built_in in (GITHUB_CAPABILITIES, GITLAB_CAPABILITIES):
        with pytest.raises(ValueError, match="unsupported flow trigger"):
            built_in.validate(graph)


def test_provider_defaults_preserve_explicit_permissions_and_inheritance() -> None:
    graph = Graph.from_yaml(
        "version: 0\nprofiles:\n  default: {}\n  isolated: {permissions: {}}\n"
        "  child: {extends: default}\nnodes: {check: {run: check}}"
    )
    profiles = graph.expanded_profiles(GITHUB_CAPABILITIES.profile_defaults)
    assert profiles["default"].runner == "ubuntu-latest"
    assert profiles["child"].permissions == {"contents": "read"}
    assert profiles["isolated"].permissions == {}
    assert graph.expanded_profiles(GITLAB_CAPABILITIES.profile_defaults)["default"].runner is None


class CustomProvider:
    name = "custom"

    def generate(self, _graph: Graph) -> GeneratedProject:
        return GeneratedProject(
            "digest",
            (GeneratedFile(PurePosixPath("ci.yml"), "owned\nnew\n", overwrite_marker="owned\n"),),
        )


def test_generated_file_metadata_protects_custom_provider_files(tmp_path: Path) -> None:
    graph = Graph.from_yaml(
        "version: 0\nprovider: {name: custom}\nprofiles: {default: {}}\n"
        "nodes: {check: {run: check}}"
    )
    path = tmp_path / "ci.yml"
    path.write_text("user-owned\n")
    project = Project(tmp_path, graph, CustomProvider())
    with pytest.raises(FileExistsError, match="unmanaged generated file"):
        project.generate()
    assert path.read_text() == "user-owned\n"
    path.write_text("owned\nold\n")
    project.generate()
    assert path.read_text() == "owned\nnew\n"
