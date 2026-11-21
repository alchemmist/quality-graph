from __future__ import annotations

import json
from dataclasses import replace

import pytest
import yaml

from qg_github.compiler import RELEASE_WORKFLOW, compile_graph, event_projection, project_graph
from quality_graph_core.graph import DependencyPolicy, Graph

SOURCE = (
    "version: 0\n"
    "runtime: {action: 'owner/runtime@1111111111111111111111111111111111111111'}\n"
    "profiles: {default: {}}\n"
    "operations: {lint: {run: make lint}, test: {run: make test}}\n"
    "flows:\n"
    "  review:\n"
    "    trigger: pull-request\n"
    "    presentation: github-pr\n"
    "    nodes: {lint: {}, test: {needs: [lint]}}\n"
    "  main:\n"
    "    trigger: {push: {branches: [main]}}\n"
    "    nodes: {lint: {}, test: {needs: [lint]}}\n"
    "  release:\n"
    "    trigger: workflow-dispatch\n"
    "    concurrency: production\n"
    "    presentation: release\n"
    "    nodes:\n"
    "      lint: {}\n"
    "      test:\n"
    "        needs: [lint]\n"
    "        checkpoint: {environment: prestable, observation-seconds: 3600}\n"
)


def test_compile_explicit_flows_separates_execution_and_release_contract() -> None:
    compiled = compile_graph(Graph.from_yaml(SOURCE))
    files = {str(item.path): item.content for item in compiled.files}
    review = yaml.safe_load(files[".github/workflows/quality-graph.yml"])
    main = yaml.safe_load(files[".github/workflows/quality-graph-push.yml"])
    manifest = json.loads(files[".quality-graph/manifest.json"])

    assert review["jobs"]["test"]["needs"] == ["lint"]
    assert "needs" not in main["jobs"]["test"]
    assert "quality-graph-release.yml" not in " ".join(files)
    assert manifest["flows"]["release"]["execution"] == "design-only"
    assert manifest["flows"]["release"]["nodes"][1]["checkpoint"]["observation-seconds"] == 3600
    collect = main["jobs"]["lint"]["steps"][1]["with"]
    assert collect["flow-id"] == "main"
    assert collect["operation-id"] == "lint"
    assert collect["presentation"] == "none"
    assert compile_graph(Graph.from_yaml(SOURCE)) == compiled


def test_explicit_flow_selection_and_named_concurrency() -> None:
    graph = Graph.from_yaml(SOURCE)
    assert event_projection(graph, "push").nodes == graph.for_flow("main").nodes
    assert project_graph(graph, "push").flow_id == "main"
    with pytest.raises(ValueError, match="original declaration"):
        compile_graph(graph.for_flow("release"))
    with pytest.raises(ValueError, match="no flow"):
        project_graph(graph, "missing")
    only_main = replace(graph, flows=(replace(graph.flows[1], concurrency="checks"),))
    files = {str(item.path): item.content for item in compile_graph(only_main).files}
    assert set(files) == {
        ".github/workflows/quality-graph-push.yml",
        ".quality-graph/manifest.json",
    }
    workflow = yaml.safe_load(files[".github/workflows/quality-graph-push.yml"])
    assert workflow["concurrency"] == {"group": "quality-graph-checks", "cancel-in-progress": False}


def test_tag_release_and_branch_checks_share_push_without_sharing_jobs() -> None:
    graph = Graph.from_yaml(SOURCE)
    review, main, release = graph.flows
    tag_plan = replace(release, trigger="push", tags=("v*",))
    graph = replace(graph, flows=(tag_plan, review, main))

    assert project_graph(graph, "push") == graph.for_flow("main")
    push = event_projection(graph, "push")
    assert push.dependencies is DependencyPolicy.NONE
    assert push.nodes == graph.for_flow("main").nodes
    pr = event_projection(graph, "pull-request")
    assert pr.dependencies is DependencyPolicy.GRAPH
    assert pr.nodes == graph.for_flow("review").nodes
    compiled = compile_graph(graph)
    assert {str(item.path) for item in compiled.files} == {
        ".github/workflows/quality-graph.yml",
        ".github/workflows/quality-graph-push.yml",
        ".github/workflows/quality-graph-publish.yml",
        ".quality-graph/manifest.json",
    }
    assert compiled.retired_if_generated == (RELEASE_WORKFLOW,)


def test_flow_command_and_unicode_title_are_bound_to_manifest_digest() -> None:
    source = SOURCE.replace("lint: {run: make lint}", "lint: {run: make lint, title: Révision}")
    original = compile_graph(Graph.from_yaml(source))
    updated = compile_graph(Graph.from_yaml(source.replace("make lint", "make lint-strict")))
    assert original.graph_digest != updated.graph_digest
    manifest = next(item.content for item in updated.files if item.path.suffix == ".json")
    assert '"title": "Révision"' in manifest
    assert json.loads(manifest)["graphDigest"] == updated.graph_digest


def test_github_rejects_unsupported_flow_combinations() -> None:
    graph = Graph.from_yaml(SOURCE)
    with pytest.raises(ValueError, match="default branch is invalid"):
        compile_graph(Graph.from_yaml(SOURCE.replace("branches: [main]", "branches: [bad..ref]")))
    with pytest.raises(ValueError, match="one pull-request"):
        compile_graph(replace(graph, flows=(*graph.flows, replace(graph.flows[0], id="other"))))
    with pytest.raises(ValueError, match="unique placement titles"):
        compile_graph(
            replace(
                graph, operations=(graph.operations[0], replace(graph.operations[1], title="Lint"))
            )
        )


def test_explicit_manifest_and_workflow_provenance_are_complete() -> None:
    compiled = compile_graph(Graph.from_yaml(SOURCE))
    files = {str(item.path): item.content for item in compiled.files}
    manifest = json.loads(files[".quality-graph/manifest.json"])
    assert manifest["manifestVersion"] == 1
    assert manifest["graphDigest"] == compiled.graph_digest
    assert manifest["_generated"].startswith("Generated by Quality Graph.")
    assert "nodes" not in manifest
    assert manifest["flows"] == {
        "review": {
            "trigger": "pull-request",
            "branches": [],
            "inputs": {},
            "dependencies": "graph",
            "presentation": "github-pr",
            "concurrency": None,
            "execution": "github-actions",
            "nodes": [
                {"id": "lint", "operation": "lint", "needs": [], "checkpoint": {}},
                {"id": "test", "operation": "test", "needs": ["lint"], "checkpoint": {}},
            ],
        },
        "main": {
            "trigger": "push",
            "branches": ["main"],
            "inputs": {},
            "dependencies": "none",
            "presentation": "none",
            "concurrency": None,
            "execution": "github-actions",
            "nodes": [
                {"id": "lint", "operation": "lint", "needs": [], "checkpoint": {}},
                {"id": "test", "operation": "test", "needs": ["lint"], "checkpoint": {}},
            ],
        },
        "release": {
            "trigger": "workflow-dispatch",
            "branches": [],
            "inputs": {},
            "dependencies": "graph",
            "presentation": "release",
            "concurrency": "production",
            "execution": "design-only",
            "nodes": [
                {"id": "lint", "operation": "lint", "needs": [], "checkpoint": {}},
                {
                    "id": "test",
                    "operation": "test",
                    "needs": ["lint"],
                    "checkpoint": {"environment": "prestable", "observation-seconds": 3600},
                },
            ],
        },
    }
    assert manifest["operations"]["lint"] == {
        "id": "lint",
        "title": "Lint",
        "profile": "default",
        "step": {"run": "make lint"},
        "result": {"adapter": "exit-code"},
        "env": {},
        "diffOnly": False,
        "policy": {
            "blocking": True,
            "blockingSeverities": ["error"],
            "approvals": {"findings": True, "files": False, "node": False},
        },
    }
    assert set(manifest["operations"]) == {"lint", "test"}
    assert compiled.retired_files == ()
    for path, flow_id, trigger, presentation in (
        (".github/workflows/quality-graph.yml", "review", "pull_request", "github-pr"),
        (".github/workflows/quality-graph-push.yml", "main", "push", "none"),
    ):
        workflow = yaml.safe_load(files[path])
        assert workflow["on"] == {trigger: {"branches": ["main"]}}
        assert workflow["jobs"]["lint"]["env"] == {
            "QG_FLOW_ID": flow_id,
            "QG_OPERATION_ID": "lint",
            "QG_NODE_ID": "lint",
            "QG_NODE_TITLE": "Lint",
            "QG_GRAPH_DIGEST": compiled.graph_digest,
        }
        inputs = workflow["jobs"]["lint"]["steps"][1]["with"]
        assert inputs == {
            "operation": "collect",
            "node-id": "lint",
            "title": "Lint",
            "adapter": "exit-code",
            "report-path": "",
            "command-outcome": "${{ steps.quality-command.outcome }}",
            "graph-digest": compiled.graph_digest,
            "approval-findings": "true" if presentation == "github-pr" else "false",
            "approval-files": "false",
            "approval-node": "false",
            "flow-id": flow_id,
            "operation-id": "lint",
            "presentation": presentation,
        }
    publisher = yaml.safe_load(files[".github/workflows/quality-graph-publish.yml"])
    assert publisher["on"]["workflow_run"]["workflows"] == ["Quality Graph"]
    assert (
        publisher["jobs"]["publish"]["steps"][0]["uses"]
        == "owner/runtime@1111111111111111111111111111111111111111"
    )


def test_required_check_requires_pr_presentation() -> None:
    graph = Graph.from_yaml(SOURCE)
    with pytest.raises(ValueError, match=r"merge\.required"):
        compile_graph(
            replace(
                graph,
                flows=(graph.flows[1],),
                provider=replace(
                    graph.provider, values={**graph.provider.values, "merge": {"required": True}}
                ),
            )
        )


def test_release_only_contract_preserves_dispatch_inputs_without_workflows() -> None:
    graph = Graph.from_yaml(SOURCE)
    release = replace(graph.flows[2], inputs={"version": {"type": "string", "required": True}})
    compiled = compile_graph(replace(graph, flows=(release,)))
    assert [str(item.path) for item in compiled.files] == [".quality-graph/manifest.json"]
    assert {str(path) for path in compiled.retired_files} == {
        ".github/workflows/quality-graph.yml",
        ".github/workflows/quality-graph-push.yml",
        ".github/workflows/quality-graph-publish.yml",
    }
    manifest = json.loads(compiled.files[0].content)
    assert manifest["flows"]["release"]["inputs"] == {
        "version": {"type": "string", "required": True}
    }


def test_required_check_accepts_enabled_pr_presentation() -> None:
    graph = Graph.from_yaml(SOURCE)
    graph = replace(
        graph,
        provider=replace(
            graph.provider, values={**graph.provider.values, "merge": {"required": True}}
        ),
    )
    assert any(
        str(item.path) == ".github/workflows/quality-graph-publish.yml"
        for item in compile_graph(graph).files
    )


SHARDS = (
    "version: 0\n"
    "runtime: {action: 'owner/runtime@1111111111111111111111111111111111111111'}\n"
    "profiles: {default: {}}\n"
    "operations:\n"
    "  chart:\n"
    "    items: [api, web, worker, cron]\n"
    '    run: ./ci/check-chart "$QG_ITEM"\n'
    "    results: {junit: 'reports/{item}.xml'}\n"
    "flows:\n"
    "  review:\n"
    "    trigger: pull-request\n"
    "    presentation: github-pr\n"
    "    nodes: {chart-api: {}, chart-web: {}, chart-worker: {}, chart-cron: {}}\n"
)


def test_shards_compile_as_four_explicit_jobs() -> None:
    graph = Graph.from_yaml(SHARDS)
    explicit = yaml.safe_load(SHARDS)
    template = explicit["operations"].pop("chart")
    for item in template.pop("items"):
        explicit["operations"][f"chart-{item}"] = {
            **template,
            "env": {"QG_ITEM": item},
            "results": {"junit": f"reports/{item}.xml"},
        }
    compiled = compile_graph(graph)
    assert compiled == compile_graph(Graph.from_yaml(yaml.safe_dump(explicit, sort_keys=False)))
    files = {str(file.path): file.content for file in compiled.files}
    jobs = yaml.safe_load(files[".github/workflows/quality-graph.yml"])["jobs"]
    for operation in explicit["operations"]:
        collect = jobs[operation]["steps"][1]["with"]
        assert (collect["flow-id"], collect["operation-id"]) == ("review", operation)


@pytest.mark.parametrize(
    ("original", "invalid", "message"),
    [
        ("[api, web, worker, cron]", "[]", "1 to 64"),
        ("[api, web, worker, cron]", str(["api"] * 65), "1 to 64"),
        ("api, web", "api, api", "unique"),
        ("api, web", "api, '../web'", "invalid operation item"),
        ("  chart:", "  chart-api: {run: other}\n  chart:", "unique"),
        ("    items:", "    env: {QG_ITEM: override}\n    items:", "cannot override"),
        ("reports/{item}.xml", "reports/shared.xml", "must contain"),
    ],
)
def test_invalid_shards(original: str, invalid: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Graph.from_yaml(SHARDS.replace(original, invalid))


def test_titled_shard_steps() -> None:
    source = SHARDS.replace(
        'run: ./ci/check-chart "$QG_ITEM"',
        "title: Check chart\n    steps: [{run: check, env: {MODE: strict}}]",
    )
    graph = Graph.from_yaml(source)
    compile_graph(graph)
    for op in graph.operations:
        assert op.title == f"Check chart ({op.environment['QG_ITEM']})"
        assert op.steps[0].environment == {"MODE": "strict"}
    with pytest.raises(ValueError, match="steps cannot override QG_ITEM"):
        Graph.from_yaml(source.replace("MODE: strict", "QG_ITEM: api"))
