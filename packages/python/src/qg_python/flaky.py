"""Repeat changed Python tests to expose order-independent flakiness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Protocol

import anyio

from qg_python.diff import added_lines_by_path, patch_for_base

MIN_ATTEMPTS = 2


class ProcessResult(Protocol):
    """Expose the subprocess state needed by the flaky gate."""

    returncode: int


async def run_test(command: list[str]) -> ProcessResult:
    """Run one pytest command without raising for a test failure."""
    return await anyio.run_process(command, check=False)


def changed_test_files(base: str) -> tuple[str, ...]:
    """Return runnable test files or affected fixture scopes in stable order."""
    selected = {
        scope
        for name in added_lines_by_path(patch_for_base(base))
        for path in (Path(name),)
        if path.is_file() and path.suffix == ".py"
        for scope in (_test_scope(path),)
        if scope is not None
    }
    return tuple(
        str(path)
        for path in sorted(selected)
        if not any(parent in selected for parent in path.parents)
    )


def _test_scope(path: Path) -> Path | None:
    if path.name == "conftest.py":
        return path.parent
    if path.name.startswith("test_") or path.name.endswith("_test.py"):
        return path
    if "tests" in path.parts:
        return Path(*path.parts[: path.parts.index("tests") + 1])
    return None


def repeat(files: tuple[str, ...], attempts: int) -> int:
    """Run changed test files repeatedly with retries disabled."""
    results = []
    for attempt in range(1, attempts + 1):
        completed = anyio.run(
            run_test,
            [sys.executable, "-m", "pytest", "-q", *files],
        )
        results.append(completed.returncode)
        if completed.returncode != 0:
            sys.stderr.write(f"changed tests failed on repeat {attempt}/{attempts}\n")
    if len(set(results)) > 1:
        sys.stderr.write("changed tests are flaky across repeated runs\n")
        return 1
    return results[0]


def main(arguments: list[str] | None = None) -> int:
    """Repeat changed Python test files selected from a Git diff."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args(arguments)
    if args.attempts < MIN_ATTEMPTS:
        parser.error("--attempts must be at least 2")
    files = changed_test_files(args.base)
    return 0 if not files else repeat(files, args.attempts)
