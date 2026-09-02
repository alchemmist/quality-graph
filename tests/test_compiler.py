import json
from pathlib import Path

import pytest
import yaml
from yamllint.config import YamlLintConfig
from yamllint.linter import run as run_yamllint

from qg_github.compiler import (
    EXECUTION_WORKFLOW,
    GENERATED_NOTICE,
    GRAPH_MANIFEST,
    PUBLICATION_WORKFLOW,
    PUSH_WORKFLOW,
    compile_graph,
    event_projection,
    project_graph,
)
from quality_graph_core.graph import Graph
from tests.test_graph import GRAPH, MAXIMAL_GRAPH, RUNTIME


def generated() -> dict[str, str]:
    project = compile_graph(Graph.from_yaml(GRAPH))
    return {str(item.path): item.content for item in project.files}


def workflow(path: object) -> dict[str, object]:
    return yaml.safe_load(generated()[str(path)])


def test_compiler_is_deterministic_and_emits_complete_contract() -> None:
    first = compile_graph(Graph.from_yaml(GRAPH))
    second = compile_graph(Graph.from_yaml(GRAPH))

    assert first == second
    assert [item.path for item in first.files] == [
        EXECUTION_WORKFLOW,
        PUSH_WORKFLOW,
        PUBLICATION_WORKFLOW,
        GRAPH_MANIFEST,
    ]
    assert all(item.content.endswith("\n") for item in first.files)
    assert generated()[str(EXECUTION_WORKFLOW)].startswith(f"# {GENERATED_NOTICE}\n")
    assert generated()[str(PUSH_WORKFLOW)].startswith(f"# {GENERATED_NOTICE}\n")
    assert generated()[str(PUBLICATION_WORKFLOW)].startswith(f"# {GENERATED_NOTICE}\n")
    assert generated()[str(GRAPH_MANIFEST)].startswith(
        f'{{\n  "_generated": "{GENERATED_NOTICE}",\n'
    )


def test_complete_compiler_output_matches_contract_snapshots() -> None:
    snapshot_root = Path(__file__).parent / "snapshots" / "compiler"

    for path, content in generated().items():
        assert content == (snapshot_root / path).read_text()

    maximal = compile_graph(Graph.from_yaml(MAXIMAL_GRAPH))
    for item in maximal.files:
        assert item.content == (snapshot_root / "maximal" / item.path).read_text()


def test_generated_workflows_pass_standard_yaml_indentation() -> None:
    configuration = YamlLintConfig(
        "{extends: default, rules: {line-length: disable, "
        "truthy: {allowed-values: ['true', 'false', 'on']}}}"
    )

    for path in (EXECUTION_WORKFLOW, PUSH_WORKFLOW, PUBLICATION_WORKFLOW):
        problems = list(run_yamllint(generated()[str(path)], configuration, str(path)))
        assert problems == []


def test_manifest_expands_profiles_and_binds_graph_digest() -> None:
    project = compile_graph(Graph.from_yaml(GRAPH))
    manifest = json.loads(generated()[str(GRAPH_MANIFEST)])

    assert manifest["graphDigest"] == project.graph_digest
    assert manifest["_generated"] == GENERATED_NOTICE
    assert len(project.graph_digest) == 64
    assert manifest["profiles"]["python"]["setup"][0]["uses"] == "actions/checkout@v7"
    assert manifest["nodes"][1]["result"] == {
        "adapter": "sarif",
        "path": "reports/lint.sarif",
    }
    assert manifest["nodes"][1]["failingLabel"]["name"] == "quality:lint"
    assert manifest["labels"]["failing"]["name"] == "quality:failed"


def test_execution_workflow_preserves_native_jobs_and_dependencies() -> None:
    value = workflow(EXECUTION_WORKFLOW)
    jobs = value["jobs"]
    lint = jobs["lint"]

    assert value["permissions"] == {"contents": "read"}
    assert set(value["on"]) == {"pull_request", "workflow_dispatch"}
    assert value["on"]["pull_request"]["branches"] == ["main"]
    assert workflow(PUSH_WORKFLOW)["on"]["push"]["branches"] == ["main"]
    assert list(jobs) == ["format", "lint"]
    assert lint["needs"] == ["format"]
    assert lint["runs-on"] == "ubuntu-latest"
    assert lint["permissions"] == {"contents": "read"}
    assert lint["env"] == {"UV_NO_SYNC": "1"}
    assert lint["steps"][2]["continue-on-error"] is True
    assert lint["steps"][3]["with"]["adapter"] == "sarif"
    assert lint["steps"][4]["uses"] == "actions/upload-artifact@v7"
    assert lint["steps"][4]["with"]["retention-days"] == 7
    assert lint["steps"][5]["run"] == 'exit "${EXIT_CODE:-2}"'


def test_custom_default_branch_changes_workflow_manifest_and_digest() -> None:
    source = GRAPH.replace("default-branch: main", "default-branch: release/stable")

    original = compile_graph(Graph.from_yaml(GRAPH))
    first = compile_graph(Graph.from_yaml(source))
    second = compile_graph(Graph.from_yaml(source))
    files = {str(item.path): item.content for item in first.files}
    execution = yaml.safe_load(files[str(EXECUTION_WORKFLOW)])
    push = yaml.safe_load(files[str(PUSH_WORKFLOW)])
    manifest = json.loads(files[str(GRAPH_MANIFEST)])

    assert first == second
    assert first.graph_digest != original.graph_digest
    assert execution["on"]["pull_request"]["branches"] == ["release/stable"]
    assert push["on"]["push"]["branches"] == ["release/stable"]
    assert manifest["defaultBranch"] == "release/stable"


def test_legacy_declaration_defaults_to_main_branch() -> None:
    source = GRAPH.replace("    default-branch: main\n", "")

    project = compile_graph(Graph.from_yaml(source))
    files = {str(item.path): item.content for item in project.files}
    execution = yaml.safe_load(files[str(EXECUTION_WORKFLOW)])
    push = yaml.safe_load(files[str(PUSH_WORKFLOW)])
    manifest = json.loads(files[str(GRAPH_MANIFEST)])

    assert execution["on"]["pull_request"]["branches"] == ["main"]
    assert push["on"]["push"]["branches"] == ["main"]
    assert "defaultBranch" not in manifest


@pytest.mark.parametrize("branch", ["x", "x" * 255])
def test_default_branch_accepts_boundary_lengths(branch: str) -> None:
    source = GRAPH.replace("default-branch: main", f"default-branch: {branch}")

    manifest = json.loads(
        next(
            item.content
            for item in compile_graph(Graph.from_yaml(source)).files
            if item.path == GRAPH_MANIFEST
        )
    )

    assert manifest["defaultBranch"] == branch


def test_publication_workflow_is_privileged_without_untrusted_checkout() -> None:
    value = workflow(PUBLICATION_WORKFLOW)
    publish = value["jobs"]["publish"]
    command = value["jobs"]["command"]
    serialized = generated()[str(PUBLICATION_WORKFLOW)]

    assert value["permissions"] == {}
    assert value["on"]["workflow_run"]["types"] == [
        "requested",
        "in_progress",
        "completed",
    ]
    assert publish["steps"][0]["with"]["operation"].endswith("'publish' || 'watch' }}")
    assert publish["permissions"]["actions"] == "read"
    assert publish["permissions"]["pull-requests"] == "write"
    assert publish["permissions"]["checks"] == "write"
    assert command["permissions"]["pull-requests"] == "write"
    assert publish["if"].endswith("workflow_run.event == 'pull_request'")
    assert command["permissions"]["actions"] == "write"
    assert "actions/checkout" not in serialized
    assert "pull_request_target" not in serialized


def test_publisher_runtime_can_roll_forward_without_changing_execution_provenance() -> None:
    publisher = "alchemmist/quality-graph@" + "b" * 40
    source = GRAPH.replace(
        f"      action: {RUNTIME}",
        f"      action: {RUNTIME}\n      publisher-action: {publisher}",
    )
    original = compile_graph(Graph.from_yaml(GRAPH))
    updated = compile_graph(Graph.from_yaml(source))
    files = {str(item.path): item.content for item in updated.files}

    assert original.graph_digest == updated.graph_digest
    assert publisher in files[str(PUBLICATION_WORKFLOW)]
    assert publisher not in files[str(EXECUTION_WORKFLOW)]
    assert publisher not in files[str(PUSH_WORKFLOW)]


def test_event_projections_select_nodes_and_parallelize_push() -> None:
    source = GRAPH.replace(
        "profiles:\n",
        "execution:\n  pull-request:\n    dependencies: graph\n"
        "  push:\n    dependencies: none\nprofiles:\n",
    ).replace(
        "labels:\n",
        "  diff:\n    title: Diff check\n    events: [pull-request]\n"
        "    needs: [lint]\n    run: git diff --check origin/main...HEAD\nlabels:\n",
    )

    original = compile_graph(Graph.from_yaml(GRAPH))
    project = compile_graph(Graph.from_yaml(source))
    files = {str(item.path): item.content for item in project.files}
    pull_request = yaml.safe_load(files[str(EXECUTION_WORKFLOW)])
    push = yaml.safe_load(files[str(PUSH_WORKFLOW)])
    manifest = json.loads(files[str(GRAPH_MANIFEST)])

    assert project.graph_digest != original.graph_digest
    assert list(pull_request["jobs"]) == ["format", "lint", "diff"]
    assert pull_request["jobs"]["lint"]["needs"] == ["format"]
    assert pull_request["jobs"]["diff"]["needs"] == ["lint"]
    assert list(push["jobs"]) == ["format", "lint"]
    assert "needs" not in push["jobs"]["format"]
    assert "needs" not in push["jobs"]["lint"]
    assert manifest["execution"] == {
        "pull-request": {"dependencies": "graph"},
        "push": {"dependencies": "none"},
    }
    assert manifest["nodes"][0]["events"] == ["pull-request", "push"]
    assert manifest["nodes"][1]["events"] == ["pull-request", "push"]
    assert manifest["nodes"][2]["events"] == ["pull-request"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            GRAPH.replace("title: Lint", "title: Lint\n    events: [schedule]"),
            "unsupported execution events",
        ),
        (
            GRAPH.replace(
                "profiles:\n",
                "execution:\n  schedule:\n    dependencies: graph\nprofiles:\n",
            ),
            "unsupported execution events",
        ),
        (
            GRAPH.replace(
                "title: Formatting", "title: Formatting\n    events: [pull-request]"
            ).replace("title: Lint", "title: Lint\n    events: [pull-request]"),
            "push event projection must contain at least one node",
        ),
        (
            GRAPH.replace(
                "title: Formatting", "title: Formatting\n    events: [pull-request]"
            ).replace("title: Lint", "title: Lint\n    events: [pull-request, push]"),
            "push event projection excludes dependencies of lint",
        ),
    ],
)
def test_event_projection_validation_rejects_invalid_provider_contracts(
    source: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        compile_graph(Graph.from_yaml(source))


def test_event_projection_rejects_unknown_requested_event() -> None:
    with pytest.raises(ValueError, match="does not support execution event"):
        event_projection(Graph.from_yaml(GRAPH), "schedule")


def test_none_dependency_policy_allows_excluded_scheduling_dependencies() -> None:
    source = GRAPH.replace(
        "profiles:\n",
        "execution:\n  push:\n    dependencies: none\nprofiles:\n",
    ).replace(
        "title: Formatting\n",
        "title: Formatting\n    events: [pull-request]\n",
    )

    graph = Graph.from_yaml(source)
    projected = project_graph(graph, "push")
    project = compile_graph(graph)
    push = yaml.safe_load(
        next(item.content for item in project.files if item.path == PUSH_WORKFLOW)
    )

    assert list(push["jobs"]) == ["lint"]
    assert "needs" not in push["jobs"]["lint"]
    assert projected.nodes[0].needs == ()


def test_compiler_preserves_optional_step_job_and_label_fields() -> None:
    configured_label = "label:\n      name: quality:lint\n      color: ff0000\n      create: true"
    source = GRAPH.replace(configured_label, "label: false")
    source = source.replace(
        'persist-credentials: "false"',
        'persist-credentials: "false"\n        env:\n          SETUP_MODE: safe',
    )
    source = source.replace(
        "runner: ubuntu-latest",
        "runner: ubuntu-latest\n    container: python:3.12\n"
        "    services:\n      redis:\n        image: redis:7",
    )
    project = compile_graph(Graph.from_yaml(source))
    files = {str(item.path): item.content for item in project.files}
    execution = yaml.safe_load(files[str(EXECUTION_WORKFLOW)])
    manifest = json.loads(files[str(GRAPH_MANIFEST)])

    assert manifest["nodes"][1]["failingLabel"] is False
    assert execution["jobs"]["format"]["steps"][0]["env"] == {"SETUP_MODE": "safe"}
    assert execution["jobs"]["format"]["container"] == "python:3.12"
    assert execution["jobs"]["format"]["services"]["redis"]["image"] == "redis:7"


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (GRAPH.replace("actions/checkout@v7", "local-action", 1), "explicit ref"),
        (
            GRAPH.replace(
                "runner: ubuntu-latest",
                "runner: ubuntu-latest\n    permissions:\n      issues: write",
            ),
            "none or read",
        ),
        (
            GRAPH.replace(
                "runner: ubuntu-latest",
                "runner: ubuntu-latest\n    permissions:\n      unknown: read",
            ),
            "unknown GitHub permission",
        ),
        (GRAPH.replace(RUNTIME, "alchemmist/quality-graph@main"), "40-character-commit"),
        (
            GRAPH.replace(
                f"      action: {RUNTIME}",
                f"      action: {RUNTIME}\n      publisher-action: mutable",
            ),
            "publisher action",
        ),
        (
            GRAPH.replace(
                f"      action: {RUNTIME}",
                f"      action: {RUNTIME}\n      publisher-action: attacker/runtime@{'b' * 40}",
            ),
            "runtime action repository",
        ),
        (GRAPH.replace("name: github", "name: gitlab"), "cannot compile provider"),
        (GRAPH.replace("title: Lint", "title: Formatting"), "unique node titles"),
        (
            GRAPH.replace("    runtime:\n", "    unknown: true\n    runtime:\n"),
            "unknown configuration",
        ),
        (GRAPH.replace("default-branch: main", "default-branch: -invalid"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: '!negative'"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: '@'"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: HEAD"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: true"), "non-empty string"),
        (
            GRAPH.replace("default-branch: main", f"default-branch: {'x' * 256}"),
            "at most 255",
        ),
        (GRAPH.replace("default-branch: main", "default-branch: feature..x"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: feature/"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: feature."), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: feature//x"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: 'feature@{x'"), "default branch"),
        (GRAPH.replace("default-branch: main", "default-branch: .hidden"), "default branch"),
        (
            GRAPH.replace("default-branch: main", "default-branch: feature/.hidden"),
            "default branch",
        ),
        (GRAPH.replace("default-branch: main", "default-branch: feature.lock/x"), "default branch"),
        (
            GRAPH.replace(
                f"  configuration:\n    default-branch: main\n"
                f"    runtime:\n      action: {RUNTIME}\n",
                "  configuration: {}\n",
            ),
            "requires a runtime object",
        ),
        (
            GRAPH.replace(
                f"      action: {RUNTIME}",
                f"      action: {RUNTIME}\n      unknown: true",
            ),
            "runtime contains unknown fields",
        ),
        (
            GRAPH.replace(
                "uses: actions/checkout@v7",
                "uses: actions/checkout@v7\n        working-directory: src",
                1,
            ),
            "cannot define",
        ),
    ],
)
def test_github_provider_rejects_platform_specific_invalid_configuration(
    source: str,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        compile_graph(Graph.from_yaml(source))
