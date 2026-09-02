"""Load and validate the declarative Quality Graph interface."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import yaml

from quality_graph_core.result import JsonValue, Severity

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

NODE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
ADMIN_ROLES = {"admin", "maintain", "write"}
MAX_TIMEOUT_MINUTES = 360
MAX_LABEL_NAME_LENGTH = 50
MAX_LABEL_DESCRIPTION_LENGTH = 100


class AdapterKind(StrEnum):
    """Represent the report adapter selected for one node."""

    EXIT_CODE = "exit-code"
    NATIVE = "native"
    SARIF = "sarif"
    JUNIT = "junit"


class DependencyPolicy(StrEnum):
    """Select how an execution event projects declared node dependencies."""

    GRAPH = "graph"
    NONE = "none"


@dataclass(frozen=True)
class Step:
    """Describe one setup or node execution step."""

    name: str | None = None
    run: str | None = None
    uses: str | None = None
    arguments: Mapping[str, str] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    working_directory: str | None = None
    shell: str | None = None

    def __post_init__(self) -> None:
        """Validate the constrained GitHub step escape hatch."""
        if (self.run is None) == (self.uses is None):
            message = "a step must define exactly one of run or uses"
            raise ValueError(message)
        if self.run is not None and not self.run.strip():
            message = "a run step must not be empty"
            raise ValueError(message)


@dataclass(frozen=True)
class Profile:
    """Describe a reusable execution environment."""

    id: str
    extends: str | None = None
    runner: str | None = "ubuntu-latest"
    setup: tuple[Step, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    permissions: Mapping[str, str] = field(default_factory=lambda: {"contents": "read"})
    timeout_minutes: int | None = None
    container: str | None = None
    services: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate profile identity and least-privilege execution policy."""
        _identifier(self.id, "profile")
        if self.runner is None and self.extends is None:
            message = "profile runner must not be empty"
            raise ValueError(message)
        if self.runner == "":
            message = "profile runner must not be empty"
            raise ValueError(message)
        if self.timeout_minutes is not None and not (
            1 <= self.timeout_minutes <= MAX_TIMEOUT_MINUTES
        ):
            message = "profile timeout must be between 1 and 360 minutes"
            raise ValueError(message)


@dataclass(frozen=True)
class ResultAdapter:
    """Describe how one node command produces its result."""

    kind: AdapterKind = AdapterKind.EXIT_CODE
    path: str | None = None

    def __post_init__(self) -> None:
        """Require a report exactly for structured adapters."""
        structured = self.kind is not AdapterKind.EXIT_CODE
        if structured != (self.path is not None):
            message = f"{self.kind.value} adapter report path is inconsistent"
            raise ValueError(message)
        if self.path is not None:
            _relative_path(self.path, "result report")


@dataclass(frozen=True)
class ApprovalPolicy:
    """Declare which reversible approval scopes a node permits."""

    findings: bool = True
    files: bool = False
    node: bool = False


@dataclass(frozen=True)
class NodePolicy:
    """Declare effective graph blocking and approval behavior."""

    blocking: bool = True
    blocking_severities: tuple[Severity, ...] = (Severity.ERROR,)
    approvals: ApprovalPolicy = ApprovalPolicy()

    def __post_init__(self) -> None:
        """Reject empty or duplicate blocking severity declarations."""
        if not self.blocking_severities:
            message = "blocking-severities must not be empty"
            raise ValueError(message)
        if len(set(self.blocking_severities)) != len(self.blocking_severities):
            message = "blocking-severities must not contain duplicates"
            raise ValueError(message)


@dataclass(frozen=True)
class LabelSpec:
    """Describe one Quality Graph-owned pull-request label."""

    name: str
    color: str = "b60205"
    description: str = "Quality Graph failure"
    create: bool = False

    def __post_init__(self) -> None:
        """Validate GitHub label bounds and color syntax."""
        if not 1 <= len(self.name) <= MAX_LABEL_NAME_LENGTH:
            message = "label name length must be between 1 and 50"
            raise ValueError(message)
        if re.fullmatch(r"[0-9a-fA-F]{6}", self.color) is None:
            message = "label color must contain six hexadecimal characters"
            raise ValueError(message)
        if len(self.description) > MAX_LABEL_DESCRIPTION_LENGTH:
            message = "label description must contain at most 100 characters"
            raise ValueError(message)


@dataclass(frozen=True)
class LabelPolicy:
    """Declare optional aggregate label management."""

    enabled: bool = False
    failing: LabelSpec | None = None

    def __post_init__(self) -> None:
        """Require an aggregate label when management is enabled."""
        if self.enabled != (self.failing is not None):
            message = "labels.enabled and labels.failing must be configured together"
            raise ValueError(message)


@dataclass(frozen=True)
class Node:
    """Describe one independently executed graph operation."""

    id: str
    title: str
    step: Step
    profile: str = "default"
    needs: tuple[str, ...] = ()
    result: ResultAdapter = ResultAdapter()
    policy: NodePolicy = NodePolicy()
    failing_label: LabelSpec | None | bool = None
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_minutes: int | None = None
    events: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate node identity and local execution overrides."""
        _identifier(self.id, "node")
        if not self.title:
            message = "node title must not be empty"
            raise ValueError(message)
        if len(set(self.needs)) != len(self.needs) or self.id in self.needs:
            message = f"node dependencies must be unique and exclude itself: {self.id}"
            raise ValueError(message)
        if len(set(self.events)) != len(self.events):
            message = f"node events must be unique: {self.id}"
            raise ValueError(message)
        for event in self.events:
            _identifier(event, f"node {self.id} event")
        if self.timeout_minutes is not None and not (
            1 <= self.timeout_minutes <= MAX_TIMEOUT_MINUTES
        ):
            message = "node timeout must be between 1 and 360 minutes"
            raise ValueError(message)


@dataclass(frozen=True)
class ProviderConfiguration:
    """Carry one provider name and its opaque validated-by-provider configuration."""

    name: str
    values: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate only the platform-independent provider identity."""
        _identifier(self.name, "provider")


@dataclass(frozen=True)
class Graph:
    """Represent a validated provisional Quality Graph declaration."""

    provider: ProviderConfiguration
    profiles: tuple[Profile, ...]
    nodes: tuple[Node, ...]
    labels: LabelPolicy = LabelPolicy()
    administrator_roles: tuple[str, ...] = ("admin",)
    version: int = 0
    execution: Mapping[str, DependencyPolicy] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate cross-reference, graph, and governance invariants."""
        if self.version != 0:
            message = f"unsupported graph version: {self.version}"
            raise ValueError(message)
        profiles = _unique_profiles(self.profiles)
        nodes = _unique_nodes(self.nodes)
        _validate_profile_references(self.profiles, profiles)
        _validate_node_references(self.nodes, profiles, nodes)
        for event in self.execution:
            _identifier(event, "execution event")
        _validate_administrator_roles(self.administrator_roles)

    def expanded_profiles(self) -> dict[str, Profile]:
        """Return profiles with inheritance resolved in declaration order."""
        source = {profile.id: profile for profile in self.profiles}
        expanded: dict[str, Profile] = {}

        def expand(profile: Profile) -> Profile:
            if profile.id in expanded:
                return expanded[profile.id]
            if profile.extends is None:
                result = profile
            else:
                parent = expand(source[profile.extends])
                result = replace(
                    profile,
                    setup=(*parent.setup, *profile.setup),
                    environment={**parent.environment, **profile.environment},
                    permissions={**parent.permissions, **profile.permissions},
                    runner=profile.runner or parent.runner,
                    timeout_minutes=profile.timeout_minutes or parent.timeout_minutes,
                    container=profile.container or parent.container,
                    services={**parent.services, **profile.services},
                    extends=None,
                )
            expanded[profile.id] = result
            return result

        for profile in self.profiles:
            expand(profile)
        return expanded

    def node_order(self) -> tuple[str, ...]:
        """Return deterministic topological order with declaration-order ties."""
        pending = {node.id: set(node.needs) for node in self.nodes}
        order: list[str] = []
        while pending:
            ready = [node.id for node in self.nodes if node.id in pending and not pending[node.id]]
            for node_id in ready:
                order.append(node_id)
                pending.pop(node_id)
                for dependencies in pending.values():
                    dependencies.discard(node_id)
        return tuple(order)

    @classmethod
    def from_yaml(cls, source: str) -> Graph:
        """Load and validate a graph declaration from YAML."""
        node = yaml.compose(source, Loader=yaml.SafeLoader)
        if node is None:
            message = "quality-graph.yml must not be empty"
            raise ValueError(message)
        _reject_duplicate_yaml_keys(node)
        value = _narrow_yaml(cast("object", yaml.safe_load(source)), "quality-graph.yml")
        return _parse_graph(_object(value, "quality-graph.yml"))


# pragma: no mutate start
def _parse_graph(data: dict[str, JsonValue]) -> Graph:
    _reject_unknown(
        data,
        {
            "version",
            "provider",
            "runtime",
            "execution",
            "profiles",
            "nodes",
            "labels",
            "administration",
        },
        "graph",
    )
    profiles = _mapping(data.get("profiles"), "profiles")
    nodes = _mapping(data.get("nodes"), "nodes")
    return Graph(
        _parse_provider(data.get("provider", "github"), data.get("runtime")),
        tuple(
            _parse_profile(name, _object(value, f"profile {name}"))
            for name, value in profiles.items()
        ),
        tuple(_parse_node(name, _object(value, f"node {name}")) for name, value in nodes.items()),
        _parse_labels(_object(data.get("labels", {}), "labels")),
        _parse_administration(_object(data.get("administration", {}), "administration")),
        _integer(data.get("version"), "graph version"),
        _parse_execution(_mapping(data.get("execution", {}), "execution")),
    )


def _parse_provider(value: JsonValue, legacy_runtime: JsonValue) -> ProviderConfiguration:
    if isinstance(value, str):
        configuration = {} if legacy_runtime is None else {"runtime": legacy_runtime}
        return ProviderConfiguration(_string(value, "provider"), configuration)
    data = _object(value, "provider")
    _reject_unknown(data, {"name", "configuration"}, "provider")
    if legacy_runtime is not None:
        message = "provider configuration cannot be combined with legacy runtime"
        raise ValueError(message)
    return ProviderConfiguration(
        _string(data.get("name"), "provider name"),
        _mapping(data.get("configuration", {}), "provider configuration"),
    )


def _parse_profile(name: str, data: dict[str, JsonValue]) -> Profile:
    known = {
        "extends",
        "runner",
        "setup",
        "env",
        "permissions",
        "timeout-minutes",
        "container",
        "services",
    }
    _reject_unknown(data, known, f"profile {name}")
    return Profile(
        name,
        _optional_string(data.get("extends"), f"profile {name} parent"),
        (
            _optional_string(data.get("runner"), f"profile {name} runner")
            if "extends" in data
            else _string(data.get("runner", "ubuntu-latest"), f"profile {name} runner")
        ),
        tuple(
            _parse_step(_object(value, f"profile {name} setup step"))
            for value in _array(data.get("setup", []), f"profile {name} setup")
        ),
        _string_mapping(data.get("env", {}), f"profile {name} env"),
        _string_mapping(
            data.get("permissions", {"contents": "read"}),
            f"profile {name} permissions",
        ),
        _optional_integer(data.get("timeout-minutes"), f"profile {name} timeout"),
        _optional_string(data.get("container"), f"profile {name} container"),
        _mapping(data.get("services", {}), f"profile {name} services"),
    )


def _parse_node(name: str, data: dict[str, JsonValue]) -> Node:
    known = {
        "title",
        "profile",
        "needs",
        "events",
        "run",
        "uses",
        "with",
        "env",
        "working-directory",
        "shell",
        "results",
        "policy",
        "label",
        "timeout-minutes",
    }
    _reject_unknown(data, known, f"node {name}")
    return Node(
        name,
        _string(data.get("title", name.replace("-", " ").title()), f"node {name} title"),
        _parse_step(data),
        _string(data.get("profile", "default"), f"node {name} profile"),
        tuple(
            _string(value, f"node {name} dependency")
            for value in _array(data.get("needs", []), f"node {name} needs")
        ),
        _parse_results(_object(data.get("results", {}), f"node {name} results")),
        _parse_policy(_object(data.get("policy", {}), f"node {name} policy")),
        _parse_node_label(data.get("label"), f"node {name} label"),
        _string_mapping(data.get("env", {}), f"node {name} env"),
        _optional_integer(data.get("timeout-minutes"), f"node {name} timeout"),
        _parse_node_events(name, data),
    )


def _parse_node_events(name: str, data: dict[str, JsonValue]) -> tuple[str, ...]:
    if "events" not in data:
        return ()
    values = _array(data["events"], f"node {name} events")
    if not values:
        message = f"node {name} events must not be empty"
        raise ValueError(message)
    return tuple(_string(value, f"node {name} event") for value in values)


def _parse_execution(data: Mapping[str, JsonValue]) -> dict[str, DependencyPolicy]:
    result: dict[str, DependencyPolicy] = {}
    for event, value in data.items():
        configuration = _object(value, f"execution event {event}")
        _reject_unknown(configuration, {"dependencies"}, f"execution event {event}")
        dependency = _string(
            configuration.get("dependencies", DependencyPolicy.GRAPH.value),
            f"execution event {event} dependencies",
        )
        try:
            result[event] = DependencyPolicy(dependency)
        except ValueError as error:
            message = f"unsupported dependency policy for execution event {event}: {dependency}"
            raise ValueError(message) from error
    return result


def _parse_step(data: dict[str, JsonValue]) -> Step:
    return Step(
        _optional_string(data.get("name"), "step name"),
        _optional_string(data.get("run"), "step run"),
        _optional_string(data.get("uses"), "step action"),
        _string_mapping(data.get("with", {}), "step arguments"),
        _string_mapping(data.get("env", {}), "step env"),
        _optional_string(data.get("working-directory"), "step working directory"),
        _optional_string(data.get("shell"), "step shell"),
    )


def _parse_results(data: dict[str, JsonValue]) -> ResultAdapter:
    _reject_unknown(data, {kind.value for kind in AdapterKind}, "results")
    if not data:
        return ResultAdapter()
    if len(data) != 1:
        message = "results must configure exactly one adapter"
        raise ValueError(message)
    name, value = next(iter(data.items()))
    kind = AdapterKind(name)
    path = None if kind is AdapterKind.EXIT_CODE else _string(value, f"{name} report path")
    return ResultAdapter(kind, path)


def _parse_policy(data: dict[str, JsonValue]) -> NodePolicy:
    _reject_unknown(data, {"blocking", "blocking-severities", "approvals"}, "node policy")
    approvals = _object(data.get("approvals", {}), "approval policy")
    _reject_unknown(approvals, {"findings", "files", "node"}, "approval policy")
    severities = tuple(
        Severity(_string(value, "blocking severity"))
        for value in _array(data.get("blocking-severities", ["error"]), "blocking severities")
    )
    return NodePolicy(
        _boolean(data.get("blocking", True), "node blocking"),
        severities,
        ApprovalPolicy(
            _boolean(approvals.get("findings", True), "finding approvals"),
            _boolean(approvals.get("files", False), "file approvals"),
            _boolean(approvals.get("node", False), "node approvals"),
        ),
    )


def _parse_labels(data: dict[str, JsonValue]) -> LabelPolicy:
    _reject_unknown(data, {"enabled", "failing"}, "labels")
    enabled = _boolean(data.get("enabled", False), "labels enabled")
    failing = data.get("failing")
    label = _parse_label(failing, "aggregate label") if failing is not None else None
    return LabelPolicy(enabled, label)


def _parse_node_label(value: JsonValue, context: str) -> LabelSpec | None | bool:
    if value is None or isinstance(value, bool):
        return value
    return _parse_label(value, context)


def _parse_label(value: JsonValue, context: str) -> LabelSpec:
    if isinstance(value, str):
        return LabelSpec(value)
    data = _object(value, context)
    _reject_unknown(data, {"name", "color", "description", "create"}, context)
    return LabelSpec(
        _string(data.get("name"), f"{context} name"),
        _string(data.get("color", "b60205"), f"{context} color"),
        _string(data.get("description", "Quality Graph failure"), f"{context} description"),
        _boolean(data.get("create", False), f"{context} create"),
    )


def _parse_administration(data: dict[str, JsonValue]) -> tuple[str, ...]:
    _reject_unknown(data, {"roles"}, "administration")
    return tuple(
        _string(value, "administrator role")
        for value in _array(data.get("roles", ["admin"]), "administrator roles")
    )


# pragma: no mutate end
def _visit_profiles(profiles: Mapping[str, Profile]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(profile_id: str) -> None:
        if profile_id in active:
            message = f"profile inheritance contains a cycle at {profile_id}"
            raise ValueError(message)
        if profile_id in visited:
            return
        active.add(profile_id)
        parent = profiles[profile_id].extends
        if parent is not None:
            visit(parent)
        active.remove(profile_id)
        visited.add(profile_id)

    for profile_id in profiles:
        visit(profile_id)


def _unique_profiles(profiles: tuple[Profile, ...]) -> dict[str, Profile]:
    result = {profile.id: profile for profile in profiles}
    if len(result) != len(profiles):
        message = "profile identifiers must be unique"
        raise ValueError(message)
    if "default" not in result:
        message = "a default profile is required"
        raise ValueError(message)
    return result


def _unique_nodes(nodes: tuple[Node, ...]) -> dict[str, Node]:
    result = {node.id: node for node in nodes}
    if len(result) != len(nodes):
        message = "node identifiers must be unique"
        raise ValueError(message)
    return result


def _validate_profile_references(
    declared: tuple[Profile, ...], profiles: Mapping[str, Profile]
) -> None:
    for profile in declared:
        if profile.extends is not None and profile.extends not in profiles:
            message = f"unknown parent profile: {profile.extends}"
            raise ValueError(message)
    _visit_profiles(profiles)


def _validate_node_references(
    declared: tuple[Node, ...],
    profiles: Mapping[str, Profile],
    nodes: Mapping[str, Node],
) -> None:
    for node in declared:
        if node.profile not in profiles:
            message = f"unknown profile for node {node.id}: {node.profile}"
            raise ValueError(message)
        unknown = set(node.needs) - nodes.keys()
        if unknown:
            message = f"unknown dependencies for node {node.id}: {', '.join(sorted(unknown))}"
            raise ValueError(message)
    _visit_nodes(nodes)


def _validate_administrator_roles(roles: tuple[str, ...]) -> None:
    if not roles or set(roles) - ADMIN_ROLES:
        message = "administrator roles must use admin, maintain, or write"
        raise ValueError(message)
    if len(set(roles)) != len(roles):
        message = "administrator roles must not contain duplicates"
        raise ValueError(message)


def _visit_nodes(nodes: Mapping[str, Node]) -> None:
    visited: set[str] = set()
    active: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in active:
            message = f"graph contains a cycle at {node_id}"
            raise ValueError(message)
        if node_id in visited:
            return
        active.add(node_id)
        for dependency in nodes[node_id].needs:
            visit(dependency)
        active.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)


def _reject_duplicate_yaml_keys(node: yaml.Node) -> None:
    if isinstance(node, yaml.MappingNode):
        observed: set[str] = set()
        for key, value in node.value:
            if not isinstance(key, yaml.ScalarNode):
                message = "YAML mapping keys must be strings"
                raise TypeError(message)
            if key.value in observed:
                message = f"duplicate YAML key: {key.value}"
                raise ValueError(message)
            observed.add(key.value)
            _reject_duplicate_yaml_keys(value)
    elif isinstance(node, yaml.SequenceNode):
        for value in node.value:
            _reject_duplicate_yaml_keys(value)


def _narrow_yaml(value: object, context: str) -> JsonValue:
    if value is None or isinstance(value, bool | float | int | str):
        return value
    if isinstance(value, list):
        return [_narrow_yaml(item, context) for item in value]
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                message = f"{context} mapping keys must be strings"
                raise TypeError(message)
            result[key] = _narrow_yaml(item, context)
        return result
    message = f"{context} contains unsupported YAML value: {type(value).__name__}"
    raise TypeError(message)


def _object(value: JsonValue, context: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        message = f"{context} must be an object"
        raise TypeError(message)
    return value


def _mapping(value: JsonValue, context: str) -> dict[str, JsonValue]:
    return _object(value, context)


def _array(value: JsonValue, context: str) -> list[JsonValue]:
    if not isinstance(value, list):
        message = f"{context} must be an array"
        raise TypeError(message)
    return value


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    return value


def _optional_string(value: JsonValue, context: str) -> str | None:
    return None if value is None else _string(value, context)


def _string_mapping(value: JsonValue, context: str) -> dict[str, str]:
    return {
        key: _string(item, f"{context}.{key}") for key, item in _mapping(value, context).items()
    }


def _integer(value: JsonValue, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{context} must be an integer"
        raise TypeError(message)
    return value


def _optional_integer(value: JsonValue, context: str) -> int | None:
    return None if value is None else _integer(value, context)


def _boolean(value: JsonValue, context: str) -> bool:
    if not isinstance(value, bool):
        message = f"{context} must be a boolean"
        raise TypeError(message)
    return value


def _reject_unknown(data: Mapping[str, JsonValue], known: Iterable[str], context: str) -> None:
    unknown = sorted(data.keys() - set(known))
    if unknown:
        message = f"{context} contains unknown fields: {', '.join(unknown)}"
        raise ValueError(message)


def _identifier(value: str, context: str) -> None:
    if NODE_ID_RE.fullmatch(value) is None:
        message = f"invalid {context} identifier: {value}"
        raise ValueError(message)


def _relative_path(value: str, context: str) -> None:
    if not value or value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
        message = f"{context} path must be repository-relative"
        raise ValueError(message)
