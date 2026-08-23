"""Reject newly introduced lint, typing, security, and coverage suppressions."""

from __future__ import annotations

import argparse
import re

from qg_python.diff import changed_files
from qg_python.report import Finding, report

SOURCE_RE = re.compile(
    r"#\s*(?:noqa|nosec|type:\s*ignore|pyright:\s*ignore|pylint:\s*(?:disable|skip-file)"
    r"|pragma:\s*no cover)",
    re.IGNORECASE,
)
CONFIG_RE = re.compile(
    r"(?:per-file-ignores|extend-per-file-ignores|extend-ignore|ignore|disable|exclude)\s*[=:]",
    re.IGNORECASE,
)
SUFFIXES = (".py", ".toml", ".yaml", ".yml", ".json")


def scan_source(path: str, source: str, added: frozenset[int]) -> tuple[Finding, ...]:
    """Find suppression directives on added lines."""
    pattern = SOURCE_RE if path.endswith(".py") else CONFIG_RE
    findings = []
    for line_number, line in enumerate(source.splitlines(), 1):
        if line_number not in added:
            continue
        match = pattern.search(line)
        if match is None:
            continue
        findings.append(
            Finding(
                path,
                line_number,
                match.start() + 1,
                f"new suppression is not allowed: {match.group().strip()}",
            )
        )
    return tuple(findings)


def main(arguments: list[str] | None = None) -> int:
    """Check changed files for newly introduced suppressions."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(arguments)
    findings = tuple(
        finding
        for changed in changed_files(args.base, SUFFIXES)
        for finding in scan_source(changed.path, changed.source, changed.added_lines)
    )
    return report(findings)
