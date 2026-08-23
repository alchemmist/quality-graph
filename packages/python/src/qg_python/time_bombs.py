"""Detect plausible Unix timestamp literals introduced on changed source lines."""

from __future__ import annotations

import argparse
import re

from qg_python.diff import changed_files
from qg_python.report import Finding, report

NUMBER_RE = re.compile(r"(?<![\w.])\d(?:_?\d){8,18}(?![\w.])")
TIMESTAMP_SCALES = (
    ("nanoseconds", 10**9),
    ("microseconds", 10**6),
    ("milliseconds", 10**3),
    ("seconds", 1),
)
EARLIEST_SECONDS = 946_684_800
LATEST_SECONDS = 7_258_118_400


def timestamp_unit(literal: str) -> str | None:
    """Classify a plausible timestamp literal by unit."""
    value = int(literal.replace("_", ""))
    for unit, scale in TIMESTAMP_SCALES:
        if EARLIEST_SECONDS * scale <= value <= LATEST_SECONDS * scale:
            return unit
    return None


def scan_source(path: str, source: str, added: frozenset[int]) -> tuple[Finding, ...]:
    """Find plausible timestamps on added lines."""
    findings = []
    for line_number, line in enumerate(source.splitlines(), 1):
        if line_number not in added:
            continue
        for match in NUMBER_RE.finditer(line):
            unit = timestamp_unit(match.group())
            if unit is not None:
                findings.append(
                    Finding(
                        path,
                        line_number,
                        match.start() + 1,
                        f"possible Unix timestamp in {unit}: {match.group()}",
                    )
                )
    return tuple(findings)


def main(arguments: list[str] | None = None) -> int:
    """Check changed Python and shell source for timestamp literals."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(arguments)
    findings = tuple(
        finding
        for changed in changed_files(args.base, (".py", ".sh"))
        for finding in scan_source(changed.path, changed.source, changed.added_lines)
    )
    return report(findings)
