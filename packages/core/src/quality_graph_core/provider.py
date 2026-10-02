"""Define the provider seam shared by platform adapters and applications."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import PurePosixPath

    from quality_graph_core.graph import Graph


@dataclass(frozen=True)
class ProfileDefaults:
    """Carry provider-owned defaults for otherwise portable profiles."""

    runner: str | None = None
    permissions: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCapabilities:
    """Validate provider-specific event, presentation and execution support."""

    events: frozenset[str]
    presentations: Mapping[str, frozenset[str]]
    execution_modes: frozenset[str]
    profile_defaults: ProfileDefaults = field(default_factory=ProfileDefaults)

    def validate(self, graph: Graph) -> None:
        """Reject unsupported capabilities before generating provider output."""
        unknown = set(graph.execution) - self.events
        for node in graph.nodes:
            unknown.update(set(node.events) - self.events)
        if unknown:
            message = f"unsupported execution events: {', '.join(sorted(unknown))}"
            raise ValueError(message)
        for flow in graph.flows:
            if flow.trigger not in self.events:
                message = f"unsupported flow trigger: {flow.trigger}"
                raise ValueError(message)
            if flow.presentation not in self.presentations:
                message = (
                    f"unsupported presentation adapter: {flow.presentation}; "
                    f"supported: {', '.join(self.presentations)}"
                )
                raise ValueError(message)
            if flow.trigger not in self.presentations[flow.presentation]:
                message = "presentation adapter is incompatible with flow trigger"
                raise ValueError(message)
            if flow.presentation == "release" and not flow.is_release:
                message = "presentation adapter is incompatible with flow trigger"
                raise ValueError(message)
            if flow.execution not in self.execution_modes:
                message = f"unsupported release execution mode: {flow.execution}"
                raise ValueError(message)


@dataclass(frozen=True)
class GeneratedFile:
    """Represent one deterministic provider output."""

    path: PurePosixPath
    content: str
    overwrite_marker: str | None = None


@dataclass(frozen=True)
class GeneratedProject:
    """Carry all deterministic outputs produced for one graph."""

    graph_digest: str
    files: tuple[GeneratedFile, ...]
    retired_files: tuple[PurePosixPath, ...] = ()
    retired_if_generated: tuple[PurePosixPath, ...] = ()


@runtime_checkable
class Provider(Protocol):
    """Compile a platform-independent graph for one hosting platform."""

    name: str

    def generate(self, graph: Graph) -> GeneratedProject:
        """Return every deterministic platform output for the graph."""
        ...


@runtime_checkable
class ProviderInitializer(Protocol):
    """Optionally supply provider-native starter declarations."""

    def starter_configuration(self, default_branch: str, preset: str) -> str:
        """Return a declaration that can be validated before writing."""
        ...
