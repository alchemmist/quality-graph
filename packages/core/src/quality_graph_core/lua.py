"""Collect a producer report from a disposable, credential-free Lua worker."""

from __future__ import annotations

import json
import sys

import anyio

from quality_graph_core.adapters import AdapterError

MAX_OUTPUT = 1_048_576


async def _run(payload: bytes) -> tuple[int, bytes, bytes]:
    with anyio.fail_after(4):
        worker = await anyio.run_process(
            [sys.executable, "-I", "-m", "quality_graph_core.lua_worker"],
            input=payload,
            env={},
            cwd="/",
            check=False,
        )
    return worker.returncode, worker.stdout, worker.stderr


def transform(code: str, report: bytes) -> bytes:
    """Run only the adapter (never the declared command) with a hard wall clock limit."""
    payload = json.dumps([code, report.decode()]).encode()
    try:
        status, output, errors = anyio.run(_run, payload)
    except TimeoutError as error:
        message = "Lua adapter exceeded its execution time limit"
        raise AdapterError(message) from error
    if status or len(output) > MAX_OUTPUT:
        detail = errors[:1000].decode(errors="replace") or "worker resource limit exceeded"
        message = f"Lua adapter failed: {detail}"
        raise AdapterError(message)
    return output
