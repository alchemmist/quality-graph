"""Repeat changed Python tests to expose order-independent flakiness."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from qg_python.diff import added_lines_by_path, patch_for_base

MIN_ATTEMPTS = 2

if TYPE_CHECKING:
    from subprocess import CompletedProcess


async def run_test(command: list[str]) -> CompletedProcess[bytes]:
    """Run one pytest command without raising for a test failure."""
    return await anyio.run_process(command, check=False)


def changed_test_files(base: str) -> tuple[str, ...]:
    """Return changed existing Python test files in deterministic order."""
    return tuple(
        sorted(
            path
            for path in added_lines_by_path(patch_for_base(base))
            if Path(path).is_file()
            and Path(path).suffix == ".py"
            and (Path(path).name.startswith("test_") or "/tests/" in f"/{path}")
        )
    )


def repeat(files: tuple[str, ...], attempts: int) -> int:
    """Run changed test files repeatedly with retries disabled."""
    for attempt in range(1, attempts + 1):
        completed = anyio.run(
            run_test,
            [sys.executable, "-m", "pytest", "-q", *files],
        )
        if completed.returncode != 0:
            sys.stderr.write(f"changed tests failed on repeat {attempt}/{attempts}\n")
            return completed.returncode
    return 0


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
