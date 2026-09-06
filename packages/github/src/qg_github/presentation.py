"""Select the GitHub PR presentation adapter from trusted flow configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from qg_github.compiler import project_graph

if TYPE_CHECKING:
    from quality_graph_core.graph import Graph


def pr_presentation_graph(graph: Graph) -> Graph | None:
    """Return PR adapter input, or disable every PR write for this declaration."""
    if graph.flows and not any(flow.presentation == "github-pr" for flow in graph.flows):
        return None
    return project_graph(graph, "pull-request")
