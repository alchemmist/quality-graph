"""Accept declaration migrations only when the trusted PR contract is unchanged."""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING
from urllib.parse import quote

from qg_github.artifacts import DeclarationMismatchError, download_results
from qg_github.compiler import compile_graph, pr_contract
from qg_github.github import GitHubError
from qg_github.presentation import pr_presentation_graph
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from qg_github.artifacts import ArtifactExpectation
    from qg_github.github import GitHubPort
    from quality_graph_core.result import Result

MAX_DECLARATION_BYTES = 1_048_576


def read_pr_results(
    port: GitHubPort, graph: Graph, expectation: ArtifactExpectation
) -> tuple[Graph, dict[str, Result]]:
    """Verify run provenance and allow only semantically identical PR declaration migrations."""
    try:
        return graph, download_results(port, expectation)
    except DeclarationMismatchError as mismatch:
        try:
            head = _head_declaration(port, expectation.head_sha)
            compiled = compile_graph(head)
            projected = pr_presentation_graph(head)
        except (GitHubError, ValueError, TypeError) as error:
            raise mismatch from error
        if (
            projected is None
            or compiled.graph_digest == expectation.graph_digest
            or pr_contract(projected) != pr_contract(graph)
        ):
            raise
        current = replace(
            expectation,
            graph_digest=compiled.graph_digest,
            flow_id=projected.flow_id,
            operation_ids={
                node.id: node.operation_id
                for node in projected.nodes
                if node.operation_id is not None
            },
        )
        return projected, download_results(port, current)


def _head_declaration(port: GitHubPort, sha: str) -> Graph:
    value = port.request("GET", f"/contents/qg.yaml?ref={quote(sha, safe='')}")
    if not isinstance(value, dict):
        message = "head declaration response must be an object"
        raise TypeError(message)
    content = value.get("content")
    if not isinstance(content, str):
        message = "head declaration must be text"
        raise TypeError(message)
    decoded = base64.b64decode("".join(content.split()), validate=True)
    if len(decoded) > MAX_DECLARATION_BYTES:
        message = "head declaration exceeds the size limit"
        raise ValueError(message)
    return Graph.from_yaml(decoded.decode())
