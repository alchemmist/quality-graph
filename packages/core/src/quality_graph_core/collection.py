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
from quality_graph_core.graph import AdapterKind, LuaAdapter
from quality_graph_core.lua import transform
from quality_graph_core.workspace import read_bounded, read_lua_file

if TYPE_CHECKING:
    from pathlib import Path

    from quality_graph_core.result import Result


def collect_report(
    context: AdapterContext,
    kind: AdapterKind,
    workspace: Path,
    report_path: str | None = None,
    *,
    lua: LuaAdapter | None = None,
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
        if kind is AdapterKind.LUA:
            if lua is None:
                message = "Lua source not configured"
                raise AdapterError(message)
            if lua.file is not None and read_lua_file(workspace, lua.file) != lua.code:
                message = "Lua script differs from the compiled declaration"
                raise AdapterError(message)
            report = read_bounded(workspace, report_path, 1_048_576)
            return adapt_native(context, transform(lua.code, report))
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
