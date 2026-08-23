"""Expose GitHub workflow generation through the core provider seam."""

from qg_github.compiler import compile_graph
from quality_graph_core.graph import Graph
from quality_graph_core.provider import GeneratedProject


class GitHubProvider:
    """Generate the complete GitHub representation of a Quality Graph."""

    name = "github"

    def generate(self, graph: Graph) -> GeneratedProject:
        """Compile a graph into GitHub workflows and trusted metadata."""
        return compile_graph(graph)


provider = GitHubProvider()

__all__ = ["GitHubProvider", "provider"]
