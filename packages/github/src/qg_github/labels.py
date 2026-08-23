"""Reconcile Quality Graph-owned pull-request labels from effective state."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import TYPE_CHECKING, cast

from qg_github.github import GitHubPort, paged
from quality_graph_core.graph import Graph, LabelSpec
from quality_graph_core.result import JsonValue, Result, ResultStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

LABEL_STATE_RE = re.compile(r"<!-- quality-graph:labels:(?P<payload>[A-Za-z0-9_-]+) -->")


def configured_label_names(graph: Graph) -> tuple[str, ...]:
    """Return every currently configured Quality Graph-owned label."""
    if not graph.labels.enabled:
        return ()
    aggregate = cast("LabelSpec", graph.labels.failing)
    names: list[str] = [aggregate.name]
    names.extend(
        node.failing_label.name for node in graph.nodes if isinstance(node.failing_label, LabelSpec)
    )
    return tuple(dict.fromkeys(names))


def label_state_marker(names: Iterable[str]) -> str:
    """Encode owned label names in the managed dashboard."""
    value = json.dumps(sorted(set(names)), separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(value).decode().rstrip("=")
    return f"<!-- quality-graph:labels:{encoded} -->"


def parse_label_state(body: str) -> frozenset[str]:
    """Decode owned label names from a managed dashboard."""
    match = LABEL_STATE_RE.search(body)
    if match is None:
        return frozenset()
    encoded = match.group("payload")
    try:
        value = cast(
            "JsonValue",
            json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))),
        )
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return frozenset()
        return frozenset(cast("list[str]", value))
    except (ValueError, json.JSONDecodeError):
        return frozenset()


def reconcile_labels(
    port: GitHubPort,
    number: int,
    graph: Graph,
    results: Mapping[str, Result],
    *,
    previous_owned: Iterable[str] = (),
) -> None:
    """Converge only Quality Graph-owned labels without touching unrelated state."""
    if not graph.labels.enabled:
        return
    current = {
        _string(item.get("name"), "pull request label")
        for item in paged(port, f"/issues/{number}/labels")
    }
    desired = _desired_labels(graph, results)
    specifications = _label_specs(graph)
    owned = set(previous_owned) | specifications.keys()
    for name in sorted(owned):
        present = name in desired
        if present and name not in current:
            specification = specifications[name]
            if specification.create:
                _ensure_label(port, specification)
            port.request("POST", f"/issues/{number}/labels", {"labels": [name]})
        elif not present and name in current:
            encoded = urllib.parse.quote(name, safe="")
            port.request("DELETE", f"/issues/{number}/labels/{encoded}")


def _desired_labels(graph: Graph, results: Mapping[str, Result]) -> set[str]:
    desired: set[str] = set()
    aggregate_failure = False
    for node in graph.nodes:
        result = results.get(node.id)
        failed = (
            node.policy.blocking
            and result is not None
            and result.status in {ResultStatus.FAILED, ResultStatus.CANCELLED}
        )
        aggregate_failure = aggregate_failure or failed
        if failed and isinstance(node.failing_label, LabelSpec):
            desired.add(node.failing_label.name)
    if aggregate_failure and graph.labels.failing is not None:
        desired.add(graph.labels.failing.name)
    return desired


def _label_specs(graph: Graph) -> dict[str, LabelSpec]:
    aggregate = cast("LabelSpec", graph.labels.failing)
    specifications: dict[str, LabelSpec] = {aggregate.name: aggregate}
    for node in graph.nodes:
        if isinstance(node.failing_label, LabelSpec):
            specifications[node.failing_label.name] = node.failing_label
    return specifications


def _ensure_label(port: GitHubPort, specification: LabelSpec) -> None:
    encoded = urllib.parse.quote(specification.name, safe="")
    if port.request("GET", f"/labels/{encoded}") is None:
        port.request(
            "POST",
            "/labels",
            {
                "name": specification.name,
                "color": specification.color.lower(),
                "description": specification.description,
            },
        )


def _string(value: JsonValue, context: str) -> str:
    if not isinstance(value, str):
        message = f"{context} must be a string"
        raise TypeError(message)
    return value
