from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest
import yaml

from qg_github.compiler import compile_graph
from quality_graph_core.graph import Graph, Step

if TYPE_CHECKING:
    from quality_graph_core.result import JsonValue

RELEASE_SOURCE = (
    "version: 0\n"
    "runtime: {action: 'owner/runtime@1111111111111111111111111111111111111111'}\n"
    "profiles: {default: {}}\n"
    "operations:\n"
    "  build:\n"
    "    steps:\n"
    "      - run: make package\n"
    "      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a\n"
    "        with: {name: package, path: dist/}\n"
    "  publish:\n"
    "    environment: {name: pypi, url: 'https://pypi.org/project/example/'}\n"
    "    permissions: {id-token: write}\n"
    "    steps:\n"
    "      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c\n"
    "        with: {name: package, path: dist/}\n"
    "      - uses: pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33\n"
    "flows:\n"
    "  release:\n"
    "    trigger: {push: {tags: ['v[0-9]+.[0-9]+.[0-9]+']}}\n"
    "    execution: github-actions\n"
    "    concurrency: publishing\n"
    "    presentation: release\n"
    "    nodes: {build: {}, publish: {needs: [build]}}\n"
)


def test_release_generates_tag_bound_jobs_with_artifact_handoff_and_oidc() -> None:
    compiled = compile_graph(Graph.from_yaml(RELEASE_SOURCE))
    files = {str(item.path): item.content for item in compiled.files}
    assert set(files) == {".github/workflows/release.yml", ".quality-graph/manifest.json"}
    workflow = yaml.safe_load(files[".github/workflows/release.yml"])
    assert workflow["on"] == {"push": {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}}
    assert workflow["concurrency"] == {
        "group": "quality-graph-publishing",
        "cancel-in-progress": False,
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert all(
        re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"])
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    )
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == ["build"]
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["environment"] == {"name": "pypi", "url": "https://pypi.org/project/example/"}
    assert publish["if"] == "startsWith(github.ref, 'refs/tags/')"
    assert [step.get("uses") for step in publish["steps"][:2]] == [
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
    ]
    assert not any(step.get("continue-on-error") for step in publish["steps"][:2])
    collector = next(step for step in publish["steps"] if step.get("id") == "quality-result")
    assert collector["with"]["presentation"] == "release"
    assert collector["with"]["command-outcome"] == "${{ job.status }}"
    manifest = json.loads(files[".quality-graph/manifest.json"])
    assert manifest["flows"]["release"]["execution"] == "github-actions"
    assert manifest["flows"]["release"]["tags"] == ["v[0-9]+.[0-9]+.[0-9]+"]
    assert manifest["operations"]["publish"]["permissions"] == {"id-token": "write"}
    assert manifest["operations"]["publish"]["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/project/example/",
    }
    assert manifest["operations"]["build"]["steps"] == [
        {"run": "make package"},
        {
            "uses": "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "with": {"name": "package", "path": "dist/"},
        },
    ]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("operations", "build", "steps"), [], "must not be empty"),
        (("operations", "build", "run"), "make other", "cannot be combined"),
        (
            ("operations", "build", "steps"),
            [{"run": "make package", "continue-on-error": True}],
            "unknown fields",
        ),
        (("flows", "release", "execution"), "other", "execution mode"),
        (("flows", "release", "trigger"), "pull-request", "execution mode"),
        (
            ("flows", "release", "trigger"),
            {"push": {"tags": [], "branches": ["main"]}},
            "exactly one",
        ),
        (("flows", "release", "trigger"), {"push": {"tags": []}}, "nonempty"),
        (("flows", "release", "trigger"), {"push": {"tags": ["!v*"]}}, "literal nonnegative"),
        (("flows", "release", "trigger"), {"push": {"tags": ["v *"]}}, "literal nonnegative"),
        (("operations", "publish", "environment"), "${{ inputs.environment }}", "literal nonempty"),
        (("operations", "publish", "environment"), " ", "literal nonempty"),
        (
            ("operations", "publish", "permissions"),
            {"unknown": "write"},
            "unknown GitHub permission",
        ),
        (("operations", "publish", "permissions"), {"contents": "admin"}, "permission must"),
        (("operations", "build", "steps"), [{"uses": "actions/checkout@v7"}], "pinned"),
        (
            ("flows", "release", "nodes", "build", "checkpoint"),
            {"environment": "pypi", "observation-seconds": 60},
            "resumable provider",
        ),
    ],
)
def test_unsafe_release_contracts_fail_before_workflow_generation(
    path: tuple[str, ...],
    value: JsonValue,
    message: str,
) -> None:
    declaration = cast("dict[str, JsonValue]", yaml.safe_load(RELEASE_SOURCE))
    target = declaration
    for key in path[:-1]:
        target = cast("dict[str, JsonValue]", target[key])
    target[path[-1]] = value
    with pytest.raises((ValueError, TypeError), match=message):
        compile_graph(Graph.from_yaml(yaml.safe_dump(declaration)))


@pytest.mark.parametrize("trigger", ["pull-request", "push"])
def test_release_privileges_cannot_be_reused_in_pr_or_main(trigger: str) -> None:
    graph = Graph.from_yaml(RELEASE_SOURCE)
    review = replace(
        graph.flows[0], trigger=trigger, tags=(), presentation="none", branches=("main",)
    )
    with pytest.raises(ValueError, match="none or read"):
        compile_graph(replace(graph, flows=(review,)))
    operations = tuple(
        replace(operation, permissions={"contents": "read"}) for operation in graph.operations
    )
    with pytest.raises(ValueError, match="environments are only allowed"):
        compile_graph(replace(graph, operations=operations, flows=(review,)))


def test_manual_release_requires_tag_ref_and_keeps_typed_inputs() -> None:
    graph = Graph.from_yaml(RELEASE_SOURCE)
    release = replace(
        graph.flows[0],
        trigger="workflow-dispatch",
        tags=(),
        inputs={"version": {"type": "string", "required": True}},
    )
    files = {
        str(item.path): item.content
        for item in compile_graph(replace(graph, flows=(release,))).files
    }
    workflow = yaml.safe_load(files[".github/workflows/release.yml"])
    assert workflow["on"] == {
        "workflow_dispatch": {"inputs": {"version": {"type": "string", "required": True}}}
    }
    assert workflow["jobs"]["build"]["if"] == "startsWith(github.ref, 'refs/tags/')"


def test_release_execution_is_opt_in_and_only_one_executable_lane_is_supported() -> None:
    graph = Graph.from_yaml(RELEASE_SOURCE)
    plan = replace(graph.flows[0], execution="design-only")
    assert [str(item.path) for item in compile_graph(replace(graph, flows=(plan,))).files] == [
        ".quality-graph/manifest.json"
    ]
    with pytest.raises(ValueError, match="one executable release"):
        compile_graph(replace(graph, flows=(*graph.flows, replace(graph.flows[0], id="other"))))


def test_primary_step_cannot_disagree_with_the_sequence() -> None:
    graph = Graph.from_yaml(RELEASE_SOURCE)
    with pytest.raises(ValueError, match="primary step"):
        replace(
            graph,
            operations=(replace(graph.operations[0], step=Step(run="true")), graph.operations[1]),
        )
