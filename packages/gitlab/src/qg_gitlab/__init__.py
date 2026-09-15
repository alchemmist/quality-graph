"""Expose the independently installed GitLab provider."""

from qg_gitlab.compiler import compile_graph, starter
from quality_graph_core.graph import Graph
from quality_graph_core.provider import GeneratedProject


class GitLabProvider:
    """Own GitLab compilation and provider-native initialization."""

    name = "gitlab"

    def generate(self, graph: Graph) -> GeneratedProject:
        """Return the complete deterministic GitLab project."""
        return compile_graph(graph)

    def starter_configuration(self, default_branch: str, preset: str) -> str:
        """Return a GitLab starter for either supported repository preset."""
        if preset not in {"oss", "internal"}:
            message = f"unsupported GitLab preset: {preset}"
            raise ValueError(message)
        return starter(default_branch)


provider = GitLabProvider()
