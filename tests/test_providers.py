import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

import pytest

from qg_cli.providers import ProviderNotInstalledError, load_provider
from quality_graph_core.graph import Graph
from quality_graph_core.provider import GeneratedFile, GeneratedProject, Provider


@dataclass(frozen=True)
class StubProvider:
    name: str = "stub"

    def generate(self, _graph: Graph) -> GeneratedProject:
        return GeneratedProject("a" * 64, (GeneratedFile(PurePosixPath("generated"), "value"),))


@dataclass(frozen=True)
class StubEntryPoint:
    value: object

    def load(self) -> object:
        return self.value


def installed(*values: object) -> tuple[StubEntryPoint, ...]:
    return tuple(StubEntryPoint(value) for value in values)


def test_provider_discovery_loads_one_structural_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = StubProvider()
    monkeypatch.setattr("qg_cli.providers.entry_points", lambda **_kwargs: installed(expected))

    provider = load_provider("stub")

    assert provider is expected


def test_provider_discovery_reports_actionable_missing_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qg_cli.providers.entry_points", lambda **_kwargs: installed())

    with pytest.raises(
        ProviderNotInstalledError,
        match=re.escape(
            "uv tool install quality-graph-cli==0.1.2 --with quality-graph-gitlab==0.1.2"
        ),
    ):
        load_provider("gitlab")


def test_provider_discovery_rejects_duplicates_and_invalid_implementations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "qg_cli.providers.entry_points",
        lambda **_kwargs: installed(StubProvider(), StubProvider()),
    )
    with pytest.raises(RuntimeError, match="multiple"):
        load_provider("stub")

    monkeypatch.setattr("qg_cli.providers.entry_points", lambda **_kwargs: installed(object()))
    with pytest.raises(TypeError, match="does not satisfy"):
        load_provider("stub")


def test_github_entry_point_is_installed() -> None:
    provider = load_provider("github")

    assert isinstance(provider, Provider)
    assert provider.name == "github"
    assert cast("object", provider).__class__.__module__.startswith("qg_github")
