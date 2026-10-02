"""Collect bounded reports consistently for every hosting provider."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from quality_graph_core.adapters import (
    AdapterContext,
    AdapterError,
    adapt_exit,
    adapt_junit,
    adapt_native,
    adapt_sarif,
    adapter_failure,
    read_report,
)
from quality_graph_core.graph import AdapterKind

if TYPE_CHECKING:
    from pathlib import Path

    from quality_graph_core.result import Result


def collect_report(
    context: AdapterContext, kind: AdapterKind, workspace: Path, report_path: str | None = None
) -> Result:
    """Read and adapt one existing report into trusted execution context."""
    try:
        if kind is AdapterKind.EXIT_CODE:
            output = ""
            if report_path is not None:
                try:
                    output = read_report(workspace, report_path).decode()
                except UnicodeDecodeError as error:
                    message = "Exit-code command output must be UTF-8"
                    raise AdapterError(message) from error
            return adapt_exit(context, output)
        if report_path is None:
            message = f"{kind.value} adapter requires a report path"
            raise AdapterError(message)
        report = read_report(workspace, report_path)
        adapters = {
            AdapterKind.NATIVE: adapt_native,
            AdapterKind.JUNIT: adapt_junit,
            AdapterKind.SARIF: adapt_sarif,
        }
        result = adapters[kind](context, report)
        return replace(result, controls=())
    except (OSError, TypeError, ValueError) as error:
        return adapter_failure(context, AdapterError(str(error)))
