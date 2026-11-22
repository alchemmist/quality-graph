"""Isolated entry point for the collection-only Lua 5.4 interpreter."""

from __future__ import annotations

import json
import resource
import sys
from importlib import import_module
from importlib.resources import files
from typing import TYPE_CHECKING

lua54 = import_module("lupa.lua54")

if TYPE_CHECKING:
    from typing import Protocol

    from quality_graph_core.result import JsonValue

    class LuaTable(Protocol):
        """Represent an opaque native table populated by the JSON bridge."""

        def __setitem__(self, key: str | int, value: LuaValue) -> None:
            """Set one JSON member on the Lua table."""
            ...

    type LuaValue = LuaTable | str | int | float | bool


def execute(code: str, report: str) -> str:
    """Expose only Lua primitives and JSON, never a Python object to adapter code."""
    runtime = lua54.LuaRuntime(max_memory=16 << 20, register_eval=False, register_builtins=False)
    bootstrap = files("quality_graph_core").joinpath("sandbox.lua").read_text()
    run, object_table, array_table, null = runtime.execute(bootstrap)

    def convert(value: JsonValue) -> LuaValue:
        if isinstance(value, (list, dict)):
            table: LuaTable = array_table() if isinstance(value, list) else object_table()
            for key, item in enumerate(value, 1) if isinstance(value, list) else value.items():
                table[key] = convert(item)
            return table
        return null if value is None else value

    def reject_constant(token: str) -> None:
        message = f"Invalid JSON constant: {token}"
        raise ValueError(message)

    def decode(text: str) -> LuaValue:
        return convert(json.loads(text, parse_constant=reject_constant))

    return str(run(code, report, decode, json.dumps))


def main() -> None:
    """Write bounded output or a short diagnostic, then discard all VM state."""
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (256 << 20, 256 << 20))
    try:
        code, report = json.load(sys.stdin)
        output = execute(code, report)
        sys.stdout.buffer.write(output.encode())
    except (lua54.LuaError, ValueError, OverflowError, RecursionError, MemoryError) as error:
        sys.stderr.write(str(error)[:1000] or "Lua memory limit exceeded")
        sys.exit(1)


if __name__ == "__main__":
    main()
