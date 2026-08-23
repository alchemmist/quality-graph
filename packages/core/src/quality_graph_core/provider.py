"""Define the provider seam shared by platform adapters and applications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import PurePosixPath

    from quality_graph_core.graph import Graph


@dataclass(frozen=True)
class GeneratedFile:
    """Represent one deterministic provider output."""

    path: PurePosixPath
    content: str


@dataclass(frozen=True)
class GeneratedProject:
    """Carry all deterministic outputs produced for one graph."""

    graph_digest: str
    files: tuple[GeneratedFile, ...]


@runtime_checkable
class Provider(Protocol):
    """Compile a platform-independent graph for one hosting platform."""

    name: str

    def generate(self, graph: Graph) -> GeneratedProject:
        """Return every deterministic platform output for the graph."""
        ...
