"""Render portable results for GitHub Job Summaries."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

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
    return _bounded("\n".join(lines).strip() + "\n")


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
    message = html.escape(finding.message)
    return f"- **{severity}**{rule}: {message}{location}"


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


def _bounded(value: str) -> str:
    if len(value) <= MAX_JOB_SUMMARY_CHARACTERS:
        return value
    omitted = len(value) - MAX_JOB_SUMMARY_CHARACTERS
    while True:
        notice = f"\n\n_Job Summary truncated; {omitted} characters omitted._\n"
        prefix = MAX_JOB_SUMMARY_CHARACTERS - len(notice)
        updated = len(value) - prefix
        if updated == omitted:
            return value[:prefix] + notice
        omitted = updated
