from pathlib import Path

import pytest
import yaml

from quality_graph_core.graph import (
    AdapterKind,
    DependencyPolicy,
    Graph,
    LabelPolicy,
    LabelSpec,
    Node,
    NodePolicy,
    Profile,
    ProviderConfiguration,
    ResultAdapter,
    Severity,
    Step,
)

RUNTIME = "alchemmist/quality-graph@" + "a" * 40
GRAPH = f"""version: 0
provider:
  name: github
  configuration:
    default-branch: main
    runtime:
      action: {RUNTIME}
profiles:
  default:
    runner: ubuntu-latest
    setup:
      - uses: actions/checkout@v7
        with:
          persist-credentials: "false"
  python:
    extends: default
    setup:
      - uses: astral-sh/setup-uv@v7
    env:
      UV_NO_SYNC: "1"
nodes:
  format:
    title: Formatting
    profile: python
    run: make fmt-check
  lint:
    title: Lint
    profile: python
    needs: [format]
    run: make lint
    results:
      sarif: reports/lint.sarif
    policy:
      blocking-severities: [error, warning]
      approvals:
        files: true
    label:
      name: quality:lint
      color: ff0000
      create: true
labels:
  enabled: true
  failing: quality:failed
administration:
  roles: [admin, maintain]
"""

NONE_PROJECTION_GRAPH = (
    GRAPH.replace(
        "profiles:\n",
        "execution:\n  pull-request:\n    dependencies: none\nprofiles:\n",
    )
    .replace("title: Formatting\n", "title: Formatting\n    events: [push]\n")
    .replace("title: Lint\n", "title: Lint\n    events: [pull-request]\n")
)

MAXIMAL_GRAPH = (
    GRAPH.replace(
        "runner: ubuntu-latest",
        "runner: ubuntu-latest\n    timeout-minutes: 45\n    container: python:3.12\n"
        "    services:\n      redis:\n        image: redis:7",
    )
    .replace("- uses: actions/checkout@v7", "- name: Checkout\n        uses: actions/checkout@v7")
    .replace(
        'persist-credentials: "false"',
        'persist-credentials: "false"\n        env:\n          CHECKOUT_MODE: safe',
    )
    .replace('UV_NO_SYNC: "1"', 'UV_NO_SYNC: "1"\n    permissions:\n      actions: none')
    .replace(
        "run: make fmt-check",
        "run: make fmt-check\n    env:\n      FORMAT_MODE: check\n    timeout-minutes: 10",
    )
    .replace("run: make lint", "run: make lint\n    working-directory: tools\n    shell: bash")
    .replace(
        "blocking-severities: [error, warning]",
        "blocking: false\n      blocking-severities: [error, warning]",
    )
    .replace("files: true", "findings: false\n        files: true\n        node: true")
    .replace("color: ff0000", "color: ff0000\n      description: Lint failed")
    .replace(
        "failing: quality:failed",
        "failing:\n    name: quality:failed\n    color: aa0000\n"
        "    description: Quality checks failed\n    create: true",
    )
)


def test_graph_loads_profiles_nodes_policies_and_labels() -> None:
    graph = Graph.from_yaml(GRAPH)

    assert graph.version == 0
    assert graph.provider.name == "github"
    assert graph.provider.values["default-branch"] == "main"
    assert graph.provider.values["runtime"] == {"action": RUNTIME}
    assert graph.node_order() == ("format", "lint")
    assert graph.nodes[1].result.kind is AdapterKind.SARIF
    assert graph.nodes[1].result.path == "reports/lint.sarif"
    assert graph.nodes[1].policy.blocking_severities == (Severity.ERROR, Severity.WARNING)
    assert graph.nodes[1].policy.approvals.files is True
    assert graph.nodes[1].failing_label == LabelSpec("quality:lint", "ff0000", create=True)
    assert graph.labels.failing == LabelSpec("quality:failed")
    assert graph.administrator_roles == ("admin", "maintain")


def test_graph_defaults_to_github_provider_for_legacy_declarations() -> None:
    provider = (
        f"provider:\n  name: github\n  configuration:\n    default-branch: main\n"
        f"    runtime:\n      action: {RUNTIME}\n"
    )
    legacy = GRAPH.replace(provider, f"runtime:\n  action: {RUNTIME}\n")
    graph = Graph.from_yaml(legacy)

    assert graph.provider == ProviderConfiguration("github", {"runtime": {"action": RUNTIME}})


def test_graph_loads_event_selections_and_dependency_policies() -> None:
    source = GRAPH.replace(
        "profiles:\n",
        "execution:\n  pull-request:\n    dependencies: graph\n"
        "  push:\n    dependencies: none\nprofiles:\n",
    ).replace("title: Lint\n", "title: Lint\n    events: [pull-request]\n")

    graph = Graph.from_yaml(source)

    assert graph.execution == {
        "pull-request": DependencyPolicy.GRAPH,
        "push": DependencyPolicy.NONE,
    }
    assert graph.nodes[0].events == ()
    assert graph.nodes[1].events == ("pull-request",)


def test_provider_configuration_rejects_legacy_runtime_and_unknown_fields() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        Graph.from_yaml(GRAPH.replace("profiles:\n", f"runtime:\n  action: {RUNTIME}\nprofiles:\n"))
    with pytest.raises(ValueError, match="unknown fields"):
        Graph.from_yaml(GRAPH.replace("  name: github", "  name: github\n  unknown: true"))


def test_complete_graph_model_matches_contract_snapshot() -> None:
    snapshot = Path(__file__).parent / "snapshots" / "graph.txt"

    assert repr(Graph.from_yaml(MAXIMAL_GRAPH)) + "\n" == snapshot.read_text()


def test_profile_inheritance_appends_setup_and_merges_mappings() -> None:
    graph = Graph.from_yaml(GRAPH)

    python = graph.expanded_profiles()["python"]

    assert [step.uses for step in python.setup] == [
        "actions/checkout@v7",
        "astral-sh/setup-uv@v7",
    ]
    assert python.environment == {"UV_NO_SYNC": "1"}
    assert python.permissions == {"contents": "read"}
    assert python.runner == "ubuntu-latest"
    assert python.extends is None


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("", "must not be empty"),
        (GRAPH.replace("version: 0", "version: 1"), "unsupported graph version"),
        (GRAPH.replace("  lint:\n", "  lint:\n  lint:\n"), "duplicate YAML key"),
        (GRAPH.replace("needs: [format]", "needs: [missing]"), "unknown dependencies"),
        (GRAPH.replace("profile: python", "profile: missing", 1), "unknown profile"),
        (GRAPH.replace("extends: default", "extends: missing"), "unknown parent profile"),
        (GRAPH.replace("needs: [format]", "needs: [lint]"), "exclude itself"),
        (GRAPH.replace("needs: [format]", "needs: [format, format]"), "must be unique"),
        (GRAPH.replace("title: Lint", "title: Lint\n    events: []"), "must not be empty"),
        (
            GRAPH.replace("title: Lint", "title: Lint\n    events: [push, push]"),
            "events must be unique",
        ),
        (
            GRAPH.replace(
                "profiles:\n",
                "execution:\n  push:\n    dependencies: invalid\nprofiles:\n",
            ),
            "unsupported dependency policy",
        ),
        (GRAPH.replace("sarif: reports/lint.sarif", "sarif: ../lint.sarif"), "repository-relative"),
    ],
)
def test_graph_rejects_invalid_declarations(source: str, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        Graph.from_yaml(source)


def test_graph_rejects_dependency_and_profile_cycles() -> None:
    dependency_cycle = GRAPH.replace(
        "run: make fmt-check",
        "needs: [lint]\n    run: make fmt-check",
    )
    profile_cycle = GRAPH.replace("runner: ubuntu-latest", "extends: python")

    with pytest.raises(ValueError, match="graph contains a cycle"):
        Graph.from_yaml(dependency_cycle)
    with pytest.raises(ValueError, match="profile inheritance contains a cycle"):
        Graph.from_yaml(profile_cycle)


@pytest.mark.parametrize(
    "replacement",
    [
        "uses: actions/checkout@v7\n    run: make lint",
        "run: ''",
    ],
)
def test_node_steps_require_one_valid_execution_form(replacement: str) -> None:
    source = GRAPH.replace("run: make lint", replacement)
    with pytest.raises(ValueError, match=r"step|action"):
        Graph.from_yaml(source)


def test_node_label_can_explicitly_disable_inherited_management() -> None:
    configured = "label:\n      name: quality:lint\n      color: ff0000\n      create: true"
    graph = Graph.from_yaml(GRAPH.replace(configured, "label: false"))

    assert graph.nodes[1].failing_label is False


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (GRAPH.replace("runner: ubuntu-latest", "runner: ''"), "runner must not be empty"),
        (
            GRAPH.replace("runner: ubuntu-latest", "timeout-minutes: 361"),
            "profile timeout",
        ),
        (
            GRAPH.replace("blocking-severities: [error, warning]", "blocking-severities: []"),
            "must not be empty",
        ),
        (
            GRAPH.replace(
                "blocking-severities: [error, warning]",
                "blocking-severities: [error, error]",
            ),
            "must not contain duplicates",
        ),
        (GRAPH.replace("color: ff0000", "color: invalid"), "label color"),
        (
            GRAPH.replace("failing: quality:failed", "failing: ''"),
            "label name length",
        ),
        (
            GRAPH.replace("title: Formatting", "title: ''"),
            "node title",
        ),
        (
            GRAPH.replace("title: Formatting", "title: Formatting\n    timeout-minutes: 361"),
            "node timeout",
        ),
        (
            GRAPH.replace("roles: [admin, maintain]", "roles: [read]"),
            "administrator roles",
        ),
        (
            GRAPH.replace("roles: [admin, maintain]", "roles: [admin, admin]"),
            "must not contain duplicates",
        ),
        (
            GRAPH.replace(
                "sarif: reports/lint.sarif",
                "sarif: reports/lint.sarif\n      junit: reports/lint.xml",
            ),
            "exactly one adapter",
        ),
        (
            GRAPH.replace("enabled: true\n  failing: quality:failed", "enabled: true"),
            "configured together",
        ),
    ],
)
def test_graph_rejects_invalid_policy_and_execution_bounds(source: str, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        Graph.from_yaml(source)


def test_core_step_accepts_provider_owned_action_fields() -> None:
    step = Step(uses="local-action", working_directory="src")

    assert step.uses == "local-action"


def test_result_adapter_requires_report_exactly_for_structured_formats() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        ResultAdapter(AdapterKind.SARIF)
    with pytest.raises(ValueError, match="inconsistent"):
        ResultAdapter(AdapterKind.EXIT_CODE, "result.json")


def test_graph_constructor_rejects_duplicate_and_missing_declarations() -> None:
    provider = ProviderConfiguration("github", {"runtime": {"action": RUNTIME}})
    profile = Profile("default")
    node = Node("test", "Tests", Step(run="make test"))

    with pytest.raises(ValueError, match="profile identifiers"):
        Graph(provider, (profile, profile), (node,))
    with pytest.raises(ValueError, match="default profile"):
        Graph(provider, (Profile("python"),), (node,))
    with pytest.raises(ValueError, match="node identifiers"):
        Graph(provider, (profile,), (node, node))


def test_direct_model_validation_covers_label_and_policy_bounds() -> None:
    with pytest.raises(ValueError, match="description"):
        LabelSpec("failure", description="x" * 101)
    with pytest.raises(ValueError, match="configured together"):
        LabelPolicy(enabled=False, failing=LabelSpec("failure"))
    with pytest.raises(ValueError, match="node timeout"):
        Node("test", "Tests", Step(run="test"), timeout_minutes=0)
    with pytest.raises(ValueError, match="blocking-severities"):
        NodePolicy(blocking_severities=())


@pytest.mark.parametrize(
    "source",
    [
        "? [one, two]\n: value",
        "1: value",
        "version: 2026-08-23",
        (f"version: 0\nruntime:\n  action: {RUNTIME}\nprofiles:\n  default: {{}}\nnodes: []\n"),
        GRAPH.replace("needs: [format]", "needs: format"),
        GRAPH.replace("title: Formatting", "title: 1"),
        GRAPH.replace("version: 0", "version: false"),
        GRAPH.replace("files: true", "files: 1"),
        GRAPH.replace("  python:\n", "  Invalid:\n", 1),
        GRAPH.replace("sarif: reports/lint.sarif", "sarif: ''"),
    ],
)
def test_graph_narrows_untrusted_yaml_types(source: str) -> None:
    with pytest.raises((TypeError, ValueError, yaml.YAMLError)):
        Graph.from_yaml(source)


def test_root_profile_requires_a_runner() -> None:
    with pytest.raises(ValueError, match="runner must not be empty"):
        Profile("default", runner=None)
