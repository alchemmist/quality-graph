"""Render deterministic source diagnostics for local and CI gates."""

from __future__ import annotations

import sys
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Finding:
    """Describe one source-located quality violation."""

    path: str
    line: int
    column: int
    message: str

    def diagnostic(self) -> str:
        """Render the shared compiler-style diagnostic format."""
        return f"{self.path}:{self.line}:{self.column}: error: {self.message}"


def report(findings: tuple[Finding, ...]) -> int:
    """Print sorted findings and return the gate exit code."""
    if findings:
        sys.stdout.write("\n".join(finding.diagnostic() for finding in sorted(findings)) + "\n")
    return int(bool(findings))
