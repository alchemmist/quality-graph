"""Discover installed hosting providers through Python package metadata."""

from __future__ import annotations

from importlib.metadata import entry_points

from quality_graph_core.provider import Provider

PROVIDER_GROUP = "qg.providers"


class ProviderNotInstalledError(ValueError):
    """Report a provider requested by configuration but absent from the environment."""


def load_provider(name: str) -> Provider:
    """Load one installed provider by its stable configuration name."""
    matches = tuple(entry_points(group=PROVIDER_GROUP, name=name))
    if not matches:
        message = (
            f"Provider '{name}' is not installed. "
            "Install it with: uv tool install quality-graph-cli "
            f"--with quality-graph-{name}"
        )
        raise ProviderNotInstalledError(message)
    if len(matches) != 1:
        message = f"Provider '{name}' has multiple installed implementations"
        raise RuntimeError(message)
    loaded = matches[0].load()
    if not isinstance(loaded, Provider):
        message = f"Provider '{name}' does not satisfy the Quality Graph provider interface"
        raise TypeError(message)
    return loaded
