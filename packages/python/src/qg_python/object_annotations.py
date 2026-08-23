"""
Reject overly broad object annotations on changed Python lines.
"""

from __future__ import annotations

import argparse
import ast

from qg_python.diff import changed_files
from qg_python.report import Finding, report


def annotation_nodes(tree: ast.AST) -> tuple[ast.expr, ...]:
    """
    Collect annotations that can contain object references.
    """
    nodes: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            arguments.extend(
                argument for argument in (node.args.vararg, node.args.kwarg) if argument is not None
            )
            nodes.extend(argument.annotation for argument in arguments if argument.annotation)
            if node.returns is not None:
                nodes.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            nodes.append(node.annotation)
    return tuple(nodes)


def object_location(annotation: ast.expr) -> tuple[int, int] | None:
    """
    Return the first direct, qualified, or forward object annotation.
    """
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id == "object":
            return node.lineno, node.col_offset
        if isinstance(node, ast.Attribute) and node.attr == "object":
            return node.lineno, node.col_offset
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
            except SyntaxError:
                continue
            if object_location(parsed.body) is not None:
                return node.lineno, node.col_offset
    return None


def scan_source(path: str, source: str, changed: frozenset[int]) -> tuple[Finding, ...]:
    """
    Find changed annotations using object in one Python source.
    """
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        return (
            Finding(path, error.lineno or 1, error.offset or 1, f"cannot parse Python: {error}"),
        )
    findings = []
    for annotation in annotation_nodes(tree):
        location = object_location(annotation)
        if location is None or location[0] not in changed:
            continue
        findings.append(
            Finding(
                path,
                location[0],
                location[1] + 1,
                f"annotation must be more specific than object: {ast.unparse(annotation)}",
            )
        )
    return tuple(sorted(set(findings)))


def main(arguments: list[str] | None = None) -> int:
    """
    Check changed Python annotations against a Git base.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args(arguments)
    findings = tuple(
        finding
        for changed in changed_files(args.base, (".py",))
        for finding in scan_source(changed.path, changed.source, changed.added_lines)
    )
    return report(findings)
