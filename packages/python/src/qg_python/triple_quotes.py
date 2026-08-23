"""Enforce readable delimiters for changed triple-quoted Python strings."""

from __future__ import annotations

import argparse
import ast
import io
import re
import tokenize
from dataclasses import dataclass

from qg_python.diff import changed_files
from qg_python.report import Finding, report

STRING_RE = re.compile(r"^(?:r|u|b|f|br|rb|fr|rf)?(?P<delimiter>'''|\"\"\")", re.IGNORECASE)


@dataclass(frozen=True)
class StringSpan:
    """Describe one tokenized triple-quoted string span."""

    start_line: int
    start_column: int
    opening_end: int
    end_line: int
    closing_start: int


def scan_source(path: str, source: str, added: frozenset[int]) -> tuple[Finding, ...]:
    """Find changed triple-quote delimiters with inline content."""
    lines = source.splitlines()
    findings = []
    fstrings: list[tuple[tokenize.TokenInfo, str | None]] = []
    docstrings = _docstring_positions(source)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.FSTRING_START:
                match = STRING_RE.match(token.string)
                fstrings.append((token, match.group("delimiter") if match is not None else None))
                continue
            if token.type == tokenize.FSTRING_END:
                start, delimiter = fstrings.pop()
                if delimiter is not None:
                    findings.extend(
                        _span_findings(
                            path,
                            lines,
                            added,
                            StringSpan(
                                start.start[0],
                                start.end[1] - len(delimiter),
                                start.end[1],
                                token.end[0],
                                token.end[1] - len(delimiter),
                            ),
                            docstrings,
                        )
                    )
                continue
            if token.type != tokenize.STRING:
                continue
            match = STRING_RE.match(token.string)
            if match is None:
                continue
            delimiter = match.group("delimiter")
            findings.extend(
                _span_findings(
                    path,
                    lines,
                    added,
                    StringSpan(
                        token.start[0],
                        token.start[1] + match.start("delimiter"),
                        token.start[1] + match.end(),
                        token.end[0],
                        token.end[1] - len(delimiter),
                    ),
                    docstrings,
                )
            )
    except (IndentationError, tokenize.TokenError) as error:
        findings.append(Finding(path, 1, 1, f"cannot tokenize Python: {error}"))
    return tuple(findings)


def _span_findings(
    path: str,
    lines: list[str],
    added: frozenset[int],
    span: StringSpan,
    docstrings: frozenset[tuple[int, int]],
) -> tuple[Finding, ...]:
    if span.start_line == span.end_line:
        if (span.start_line, span.start_column) in docstrings:
            return ()
        return (
            (
                Finding(
                    path,
                    span.start_line,
                    span.start_column + 1,
                    "one-line string must use quotes",
                ),
            )
            if span.start_line in added
            else ()
        )
    findings = []
    if span.start_line in added and lines[span.start_line - 1][span.opening_end :].strip():
        findings.append(
            Finding(path, span.start_line, span.start_column + 1, "triple quote must open alone")
        )
    if span.end_line in added and lines[span.end_line - 1][: span.closing_start].strip():
        findings.append(
            Finding(path, span.end_line, span.closing_start + 1, "triple quote must close alone")
        )
    return tuple(findings)


def _docstring_positions(source: str) -> frozenset[tuple[int, int]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    positions = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            positions.append((first.value.lineno, first.value.col_offset))
    return frozenset(positions)


def main(arguments: list[str] | None = None) -> int:
    """Check changed Python triple-quoted strings."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(arguments)
    findings = tuple(
        finding
        for changed in changed_files(args.base, (".py",))
        for finding in scan_source(changed.path, changed.source, changed.added_lines)
    )
    return report(findings)
