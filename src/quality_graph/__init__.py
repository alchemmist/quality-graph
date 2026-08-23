"""Public package metadata for Quality Graph."""

from importlib.metadata import version

from quality_graph.result import Result

__version__ = version("quality-graph")

__all__ = ["Result", "__version__"]
