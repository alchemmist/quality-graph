"""Manage one repository through the declarative Quality Graph seam."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from quality_graph.compiler import GeneratedProject, compile_graph
from quality_graph.graph import Graph

CONFIGURATION_PATH = Path("quality-graph.yml")


@dataclass(frozen=True)
class ValidationReport:
    """Describe generated files that are missing or stale."""

    problems: tuple[str, ...] = ()

    @property
    def current(self) -> bool:
        """Return whether every generated file matches the declaration."""
        return not self.problems


@dataclass(frozen=True)
class Project:
    """Expose graph generation and freshness through one repository-root interface."""

    root: Path
    graph: Graph

    @classmethod
    def open(cls, root: Path) -> Self:
        """Load a project from its committed declaration."""
        configuration = root / CONFIGURATION_PATH
        if not configuration.is_file():
            message = f"Quality Graph declaration does not exist: {configuration}"
            raise FileNotFoundError(message)
        return cls(root, Graph.from_yaml(configuration.read_text()))

    def render(self) -> GeneratedProject:
        """Compile the project without mutating its repository."""
        return compile_graph(self.graph)

    def generate(self) -> GeneratedProject:
        """Write every deterministic generated file."""
        generated = self.render()
        for item in generated.files:
            path = self.root / item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.content)
        return generated

    def validate(self) -> ValidationReport:
        """Compare generated output without mutating files."""
        problems: list[str] = []
        for item in self.render().files:
            path = self.root / item.path
            if not path.is_file():
                problems.append(f"missing generated file: {item.path}")
            elif path.read_text() != item.content:
                problems.append(f"stale generated file: {item.path}")
        return ValidationReport(tuple(problems))

    @classmethod
    def initialize(
        cls,
        root: Path,
        runtime_action: str,
        *,
        preset: Literal["oss", "internal"] = "oss",
        force: bool = False,
    ) -> Self:
        """Create one understandable starter declaration."""
        configuration = root / CONFIGURATION_PATH
        if configuration.exists() and not force:
            message = f"Refusing to replace existing declaration: {configuration}"
            raise FileExistsError(message)
        root.mkdir(parents=True, exist_ok=True)
        configuration.write_text(_starter_configuration(runtime_action, preset))
        return cls.open(root)


def _starter_configuration(runtime_action: str, preset: Literal["oss", "internal"]) -> str:
    runner = "ubuntu-latest" if preset == "oss" else "self-hosted"
    return f"""version: 0
runtime:
  action: {runtime_action}

profiles:
  default:
    runner: {runner}
    setup:
      - uses: actions/checkout@v7
        with:
          persist-credentials: "false"

nodes:
  quality:
    title: Quality
    run: make check
"""
