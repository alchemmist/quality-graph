"""Read changed repository lines without depending on a hosting provider."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import anyio

BASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]*$")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
GIT = shutil.which("git")


@dataclass(frozen=True)
class ChangedFile:
    """Carry current source and added destination lines for one changed file."""

    path: str
    source: str
    added_lines: frozenset[int]


def validated_base(value: str) -> str:
    """Return a ref safe to pass as one argv item to Git."""
    if BASE_RE.fullmatch(value) is None:
        message = f"invalid base ref: {value}"
        raise ValueError(message)
    return value


def patch_for_base(base: str) -> str:
    """Return a zero-context patch from the merge base to HEAD."""
    if GIT is None:
        message = "git executable is required"
        raise RuntimeError(message)
    completed = anyio.run(
        anyio.run_process,
        [
            GIT,
            "diff",
            "--unified=0",
            "--no-ext-diff",
            "--diff-filter=ACMR",
            f"{validated_base(base)}...HEAD",
            "--",
        ],
    )
    return completed.stdout.decode()


def added_lines_by_path(patch: str) -> dict[str, frozenset[int]]:
    """Parse added destination line numbers from a unified Git patch."""
    path: str | None = None
    line_number: int | None = None
    result: dict[str, set[int]] = {}
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            path = None
            line_number = None
            continue
        if line.startswith("+++ b/"):
            path = line[6:]
            result.setdefault(path, set())
            continue
        match = HUNK_RE.match(line)
        if match is not None:
            line_number = int(match.group(1))
            continue
        if path is None or line_number is None or line.startswith("\\ No newline"):
            continue
        if line.startswith("+"):
            result[path].add(line_number)
            line_number += 1
        elif not line.startswith("-"):
            line_number += 1
    return {path: frozenset(lines) for path, lines in result.items() if lines}


def changed_files(base: str, suffixes: tuple[str, ...]) -> tuple[ChangedFile, ...]:
    """Load changed existing files matching the selected suffixes."""
    result = []
    for path, lines in added_lines_by_path(patch_for_base(base)).items():
        source_path = Path(path)
        if source_path.suffix.lower() not in suffixes or not source_path.is_file():
            continue
        result.append(ChangedFile(path, source_path.read_text(), lines))
    return tuple(sorted(result, key=lambda item: item.path))
