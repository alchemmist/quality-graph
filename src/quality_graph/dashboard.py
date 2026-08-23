"""Aggregate portable node results into one bounded pull-request dashboard."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from quality_graph.comments import GITHUB_COMMENT_BODY_LIMIT, marker
from quality_graph.controls import render_control
from quality_graph.result import Control, Result, ResultStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from quality_graph.graph import Graph

ARTIFACT_ATTEMPT_RE = re.compile(r"-(?P<attempt>[1-9][0-9]*)$")
DASHBOARD_MARKER = "dashboard"


@dataclass(frozen=True)
class DashboardRow:
    """Represent one stable compact graph node row."""

    node_id: str
    title: str
    status: ResultStatus
    metric: str
    summary_url: str
    logs_url: str


@dataclass(frozen=True)
class DashboardControlGroup:
    """Group semantic administrator controls for one node."""

    node_id: str
    title: str
    controls: tuple[Control, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DashboardModel:
    """Carry all deterministic data rendered in a managed dashboard."""

    status: ResultStatus
    message: str
    run_id: int
    run_attempt: int
    head_sha: str
    rows: tuple[DashboardRow, ...]
    control_groups: tuple[DashboardControlGroup, ...] = ()


@dataclass(frozen=True)
class DashboardRun:
    """Identify one workflow attempt and its public URL."""

    id: int
    attempt: int
    head_sha: str
    url: str


def load_results(directory: Path) -> dict[str, Result]:
    """Load the newest artifact attempt for every node."""
    results: dict[str, Result] = {}
    attempts: dict[str, int] = {}
    if not directory.exists():
        return results
    for path in sorted(directory.rglob("*.json")):
        result = Result.from_json(path.read_text())
        relative = path.relative_to(directory)
        artifact_name = relative.parts[0] if len(relative.parts) > 1 else ""
        match = ARTIFACT_ATTEMPT_RE.search(artifact_name)
        attempt = int(match.group("attempt")) if match is not None else 0
        if attempt >= attempts.get(result.node_id, -1):
            results[result.node_id] = result
            attempts[result.node_id] = attempt
    return results


def aggregate_status(statuses: Iterable[ResultStatus]) -> ResultStatus:
    """Derive graph status with pending state taking precedence over failure."""
    observed = set(statuses)
    if ResultStatus.IN_PROGRESS in observed or ResultStatus.WAITING in observed:
        return ResultStatus.IN_PROGRESS
    if ResultStatus.FAILED in observed or ResultStatus.CANCELLED in observed:
        return ResultStatus.FAILED
    return ResultStatus.PASSED


def final_dashboard(
    graph: Graph,
    results: Mapping[str, Result],
    run: DashboardRun,
) -> DashboardModel:
    """Build a final dashboard in declaration order."""
    rows: list[DashboardRow] = []
    groups: list[DashboardControlGroup] = []
    for node in graph.nodes:
        result = results.get(node.id)
        status = result.status if result is not None else ResultStatus.SKIPPED
        rows.append(
            DashboardRow(
                node.id,
                node.title,
                status,
                dashboard_metric(result),
                f"{run.url}#quality-graph-{node.id}",
                run.url,
            )
        )
        if result is not None and (result.controls or result.notes):
            groups.append(
                DashboardControlGroup(
                    node.id,
                    node.title,
                    result.controls,
                    result.notes,
                )
            )
    return DashboardModel(
        aggregate_status(
            row.status for node, row in zip(graph.nodes, rows, strict=True) if node.policy.blocking
        ),
        "Detailed diagnostics and metrics are available in each Job Summary.",
        run.id,
        run.attempt,
        run.head_sha,
        tuple(rows),
        tuple(groups),
    )


def pending_dashboard(
    graph: Graph,
    run: DashboardRun,
) -> DashboardModel:
    """Build an early dashboard before portable results are available."""
    rows = tuple(
        DashboardRow(
            node.id,
            node.title,
            ResultStatus.WAITING,
            "—",
            f"{run.url}#quality-graph-{node.id}",
            run.url,
        )
        for node in graph.nodes
    )
    return DashboardModel(
        ResultStatus.IN_PROGRESS,
        "The current Quality Graph run is in progress.",
        run.id,
        run.attempt,
        run.head_sha,
        rows,
    )


def render_dashboard(model: DashboardModel) -> str:
    """Render a complete dashboard while retaining high-value controls."""
    rendered = _render(model)
    if _fits(rendered):
        return rendered
    selected: dict[int, set[int]] = {index: set() for index, _ in enumerate(model.control_groups)}
    candidates = sorted(
        (
            (control.kind.value == "file", group_index, control_index)
            for group_index, group in enumerate(model.control_groups)
            for control_index, control in enumerate(group.controls)
        ),
        key=lambda candidate: candidate[0],
    )
    for _, group_index, control_index in candidates:
        proposed = {index: set(indices) for index, indices in selected.items()}
        proposed[group_index].add(control_index)
        groups = _selected_groups(model, proposed)
        candidate = _render(replace(model, control_groups=groups))
        if _fits(candidate):
            selected = proposed
    return _render(replace(model, control_groups=_selected_groups(model, selected)))


def dashboard_metric(result: Result | None) -> str:
    """Render at most two Markdown-safe metrics for one row."""
    if result is None or not result.metrics:
        return "—"
    return " · ".join(
        f"{_table(metric.label)}: {_table(metric.value)}" for metric in result.metrics[:2]
    )


def _render(model: DashboardModel) -> str:
    lines = [
        f"## {_status_icon(model.status)} Quality Graph",
        "",
        model.message,
        "",
        "| Check | Status | Metrics | Details |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {_table(row.title)} | {_status_icon(row.status)} {row.status.value} | "
        f"{row.metric} | [Summary]({row.summary_url}) · [Logs]({row.logs_url}) |"
        for row in model.rows
    )
    if model.control_groups:
        lines.extend(("", "<details><summary>For repository administrators</summary>", ""))
        for group in model.control_groups:
            lines.extend((f"#### {html.escape(group.title)}", ""))
            lines.extend(render_control(control) for control in group.controls)
            lines.extend(html.escape(note) for note in group.notes)
        lines.extend(("", "</details>"))
    head = html.escape(model.head_sha)
    lines.extend(("", f"Run `{model.run_id}` attempt `{model.run_attempt}` · head `{head}`"))
    return "\n".join(lines).strip() + "\n"


def _selected_groups(
    model: DashboardModel,
    selected: Mapping[int, set[int]],
) -> tuple[DashboardControlGroup, ...]:
    summary_urls = {row.node_id: row.summary_url for row in model.rows}
    groups: list[DashboardControlGroup] = []
    for group_index, group in enumerate(model.control_groups):
        indices = selected[group_index]
        controls = tuple(
            control for index, control in enumerate(group.controls) if index in indices
        )
        omitted = len(group.controls) - len(controls)
        notes = group.notes
        if omitted:
            summary = summary_urls.get(group.node_id)
            details = f"[{group.title} Job Summary]({summary})" if summary else "the Job Summary"
            notes = (*notes, f"{omitted} additional actions are available in {details}.")
        groups.append(DashboardControlGroup(group.node_id, group.title, controls, notes))
    return tuple(groups)


def _status_icon(status: ResultStatus) -> str:
    return {
        ResultStatus.WAITING: "⏳",
        ResultStatus.IN_PROGRESS: "🚀",
        ResultStatus.PASSED: "✅",
        ResultStatus.FAILED: "❌",
        ResultStatus.SKIPPED: "⏭️",
        ResultStatus.CANCELLED: "🚫",
    }[status]


def _table(value: str) -> str:
    return html.escape(value).replace("|", "&#124;").replace("\n", "<br>")


def _fits(body: str) -> bool:
    return len(f"{marker(DASHBOARD_MARKER)}\n\n{body}") <= GITHUB_COMMENT_BODY_LIMIT
