"""Compute effective node state from findings, policies, and approvals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from quality_graph_core.result import (
    Control,
    ControlKind,
    FailureKind,
    Finding,
    Result,
    ResultStatus,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Set as AbstractSet

    from quality_graph_core.graph import ApprovalPolicy, Graph, Node


@dataclass(frozen=True, order=True)
class ApprovalTarget:
    """Identify one semantic reversible approval target."""

    kind: ControlKind
    target: str

    @property
    def key(self) -> str:
        """Return the stable ledger key for this target."""
        return f"{self.kind.value}:{self.target}"

    @classmethod
    def from_key(cls, value: str) -> ApprovalTarget:
        """Parse one stable ledger key."""
        kind, separator, target = value.partition(":")
        if not separator or not target:
            message = f"invalid approval target: {value}"
            raise ValueError(message)
        return cls(ControlKind(kind), target)


@dataclass(frozen=True)
class EffectiveGraph:
    """Carry approval-adjusted results and active approval targets."""

    results: Mapping[str, Result]
    targets: frozenset[ApprovalTarget]
    approvals: frozenset[ApprovalTarget]


def effective_graph(
    graph: Graph,
    results: Mapping[str, Result],
    approvals: AbstractSet[ApprovalTarget],
) -> EffectiveGraph:
    """Apply only approvals that still match current graph findings."""
    targets = frozenset(
        target for node in graph.nodes for target in _node_targets(node, results.get(node.id))
    )
    active = frozenset(approvals) & targets
    effective = {
        node.id: _effective_result(node, result, active)
        for node in graph.nodes
        for result in (results.get(node.id),)
        if result is not None
    }
    return EffectiveGraph(effective, targets, active)


def policy_controls(
    node_id: str,
    findings: tuple[Finding, ...],
    policy: ApprovalPolicy,
    approvals: AbstractSet[ApprovalTarget] = frozenset(),
) -> tuple[Control, ...]:
    """Derive canonical semantic controls from findings and graph policy."""
    targets: list[ApprovalTarget] = []
    if policy.findings:
        targets.extend(ApprovalTarget(ControlKind.FINDING, finding.id) for finding in findings)
    if policy.files:
        targets.extend(
            ApprovalTarget(ControlKind.FILE, path)
            for path in dict.fromkeys(
                finding.location.path for finding in findings if finding.location is not None
            )
        )
    if policy.node:
        targets.append(ApprovalTarget(ControlKind.NODE, node_id))
    return tuple(
        Control(target.kind, target.target, checked=target in approvals) for target in targets
    )


def _node_targets(node: Node, result: Result | None) -> tuple[ApprovalTarget, ...]:
    if result is None:
        return ()
    return tuple(
        ApprovalTarget(control.kind, control.target)
        for control in policy_controls(node.id, result.findings, node.policy.approvals)
    )


def _effective_result(
    node: Node,
    result: Result,
    approvals: AbstractSet[ApprovalTarget],
) -> Result:
    controls = policy_controls(node.id, result.findings, node.policy.approvals, approvals)
    if result.failure_kind is not FailureKind.QUALITY:
        return replace(result, controls=controls)
    node_target = ApprovalTarget(ControlKind.NODE, node.id)
    if node_target in approvals:
        return _passed(result, controls)
    blocking = tuple(
        finding
        for finding in result.findings
        if finding.severity in node.policy.blocking_severities
        and ApprovalTarget(ControlKind.FINDING, finding.id) not in approvals
        and (
            finding.location is None
            or ApprovalTarget(ControlKind.FILE, finding.location.path) not in approvals
        )
    )
    if result.findings and not blocking:
        return _passed(result, controls)
    return replace(result, controls=controls)


def _passed(result: Result, controls: tuple[Control, ...]) -> Result:
    note = "The effective result passes because every blocking quality finding is approved."
    return replace(
        result,
        status=ResultStatus.PASSED,
        failure_kind=None,
        controls=controls,
        notes=(*result.notes, note),
    )
