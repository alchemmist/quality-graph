"""
Reject Python comments outside narrowly allowed tool directives.
"""

from __future__ import annotations

import argparse
import re
import tokenize
from pathlib import Path

from qg_python.report import Finding, report

ALLOWED_COMMENT = re.compile(
    r"^#\s*(?:noqa|nosec|type:\s*(?:ignore|noqa)|pragma:\s*no mutate)\b",
    re.IGNORECASE,
)


def python_files(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """
    Return sorted Python files below selected roots.
    """
    return tuple(
        sorted(
            path
            for root in roots
            for path in root.rglob("*.py")
            if not any(part in {".venv", "__pycache__", "mutants"} for part in path.parts)
        )
    )


def scan_file(path: Path) -> tuple[Finding, ...]:
    """
    Return disallowed comment tokens in one Python file.
    """
    findings = []
    with tokenize.open(path) as source:
        for token in tokenize.generate_tokens(source.readline):
            if token.type != tokenize.COMMENT:
                continue
            text = token.string
            if text.startswith("#!") or ALLOWED_COMMENT.match(text):
                continue
            findings.append(
                Finding(
                    path.as_posix(),
                    token.start[0],
                    token.start[1] + 1,
                    f"code comment is not allowed: {text}",
                )
            )
    return tuple(findings)


def main(arguments: list[str] | None = None) -> int:
    """
    Scan configured roots and return a process exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", type=Path, nargs="+")
    args = parser.parse_args(arguments)
    findings = tuple(
        finding for path in python_files(tuple(args.roots)) for finding in scan_file(path)
    )
    return report(findings)
