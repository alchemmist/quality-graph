"""Public package metadata for Quality Graph Core."""

from importlib.metadata import version

from quality_graph_core.graph import Graph
from quality_graph_core.provider import GeneratedFile, GeneratedProject, Provider
from quality_graph_core.result import Result

__version__ = version("quality-graph-core")

__all__ = ["GeneratedFile", "GeneratedProject", "Graph", "Provider", "Result", "__version__"]
