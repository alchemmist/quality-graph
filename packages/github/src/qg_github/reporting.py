"""Render portable results for GitHub Job Summaries."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from qg_github.controls import render_control
from quality_graph_core.result import Finding, Result, ResultStatus

if TYPE_CHECKING:
    from pathlib import Path

MAX_SUMMARY_FINDINGS = 50
MAX_JOB_SUMMARY_CHARACTERS = 1_000_000


def render_job_summary(result: Result) -> str:
    """Render one complete bounded GitHub Job Summary."""
    lines = [
        f'<a id="quality-graph-{html.escape(result.node_id)}"></a>',
        f"## {_status_icon(result.status)} {html.escape(result.title)}",
    ]
    if result.summary:
        lines.extend(("", result.summary))
    if result.metrics:
        lines.extend(("", "| Metric | Value |", "| --- | --- |"))
        lines.extend(
            f"| {_table(metric.label)} | {_table(metric.value)} |" for metric in result.metrics
        )
    if result.findings:
        lines.extend(("", "### Findings", ""))
        lines.extend(_finding_line(finding) for finding in result.findings[:MAX_SUMMARY_FINDINGS])
        omitted = len(result.findings) - MAX_SUMMARY_FINDINGS
        if omitted > 0:
            notice = f"_{omitted} additional findings are available in the result artifact._"
            lines.extend(("", notice))
    if result.diagnostics:
        lines.extend(("", "### Diagnostics", ""))
        for diagnostic in result.diagnostics:
            kind = html.escape(diagnostic.kind.value)
            message = html.escape(diagnostic.message)
            lines.append(f"- **{kind}:** {message}")
            if diagnostic.detail:
                lines.extend(("", "```text", _code(diagnostic.detail), "```"))
    if result.notes:
        lines.extend(("", "### Notes", ""))
        lines.extend(f"- {html.escape(note)}" for note in result.notes)
    body = "\n".join(lines).strip() + "\n"
    return _bounded_with_controls(body, result)


def append_job_summary(path: Path, result: Result) -> None:
    """Append one rendered result to an explicit summary sink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as summary:
        summary.write(render_job_summary(result))


def _finding_line(finding: Finding) -> str:
    location = ""
    if finding.location is not None:
        location = f" — `{html.escape(finding.location.path)}:{finding.location.start_line}`"
    rule = f" `{html.escape(finding.rule_id)}`" if finding.rule_id else ""
    severity = html.escape(finding.severity.value)
    finding_id = html.escape(finding.id)
    message = html.escape(finding.message)
    return f"- `{finding_id}` — **{severity}**{rule}: {message}{location}"


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


def _code(value: str) -> str:
    return value.replace("```", "` ` `")


def _bounded_with_controls(body: str, result: Result) -> str:
    if not result.controls:
        return _bounded(body, MAX_JOB_SUMMARY_CHARACTERS)
    minimum = _control_section((), len(result.controls))
    bounded_body = _bounded(body, MAX_JOB_SUMMARY_CHARACTERS - len(minimum) - 2)
    rendered: list[str] = []
    for control in result.controls:
        candidate = [*rendered, render_control(control, show_commands=True)]
        omitted = len(result.controls) - len(candidate)
        section = _control_section(candidate, omitted)
        if len(f"{bounded_body.rstrip()}\n\n{section}") > MAX_JOB_SUMMARY_CHARACTERS:
            break
        rendered = candidate
    omitted = len(result.controls) - len(rendered)
    return f"{bounded_body.rstrip()}\n\n{_control_section(rendered, omitted)}"


def _control_section(controls: list[str] | tuple[str, ...], omitted: int) -> str:
    lines = ["<details><summary>For repository administrators</summary>", ""]
    lines.extend(controls)
    if omitted:
        lines.extend(
            (
                "",
                f"_{omitted} additional actions are available in the result artifact._",
            )
        )
    lines.extend(("", "</details>"))
    return "\n".join(lines).strip() + "\n"


def _bounded(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    omitted = len(value) - maximum
    while True:
        notice = f"\n\n_Job Summary truncated; {omitted} characters omitted._\n"
        prefix = maximum - len(notice)
        updated = len(value) - prefix
        if updated == omitted:
            return value[:prefix] + notice
        omitted = updated
