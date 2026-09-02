"""Manage one repository through the declarative Quality Graph seam."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal, Self

from qg_cli.providers import load_provider
from quality_graph_core.graph import Graph

if TYPE_CHECKING:
    from quality_graph_core.provider import GeneratedProject, Provider

CONFIGURATION_PATH = Path("qg.yaml")
LEGACY_CONFIGURATION_PATH = Path("quality-graph.yml")
PRETTIER_IGNORE_PATH = Path(".prettierignore")
PRETTIER_BLOCK_START = "# Quality Graph generated files (managed by qg)"
PRETTIER_BLOCK_END = "# End Quality Graph generated files"


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
    provider: Provider

    @classmethod
    def open(cls, root: Path) -> Self:
        """Load a project from its committed declaration."""
        configuration = _configuration_path(root)
        if not configuration.is_file():
            message = f"Quality Graph declaration does not exist: {configuration}"
            raise FileNotFoundError(message)
        graph = Graph.from_yaml(configuration.read_text())
        return cls(root, graph, load_provider(graph.provider.name))

    def render(self) -> GeneratedProject:
        """Compile the project without mutating its repository."""
        return self.provider.generate(self.graph)

    def generate(self) -> GeneratedProject:
        """Write every deterministic generated file."""
        generated = self.render()
        prettier_ignore = self._prettier_ignore(generated)
        for item in generated.files:
            path = self.root / item.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.content)
        ignore_path = self.root / PRETTIER_IGNORE_PATH
        if not ignore_path.is_file() or ignore_path.read_text() != prettier_ignore:
            ignore_path.write_text(prettier_ignore)
        return generated

    def generated_files(self) -> tuple[PurePosixPath, ...]:
        """Return stable repository-relative compiler-owned paths."""
        return tuple(item.path for item in self.render().files)

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

    def _prettier_ignore(self, generated: GeneratedProject) -> str:
        path = self.root / PRETTIER_IGNORE_PATH
        current = path.read_text() if path.is_file() else ""
        lines = current.splitlines(keepends=True)
        starts = [
            index for index, line in enumerate(lines) if line.rstrip("\r\n") == PRETTIER_BLOCK_START
        ]
        ends = [
            index for index, line in enumerate(lines) if line.rstrip("\r\n") == PRETTIER_BLOCK_END
        ]
        if len(starts) != len(ends) or len(starts) > 1 or (starts and ends[0] < starts[0]):
            message = f"Malformed Quality Graph block in {path}"
            raise ValueError(message)
        block = (
            "\n".join(
                (
                    PRETTIER_BLOCK_START,
                    *(str(item.path) for item in generated.files),
                    PRETTIER_BLOCK_END,
                )
            )
            + "\n"
        )
        if starts:
            return "".join((*lines[: starts[0]], block, *lines[ends[0] + 1 :]))
        if not current:
            return block
        if current.endswith(("\n", "\r")):
            return current + "\n" + block
        return current + "\n\n" + block

    @classmethod
    def initialize(
        cls,
        root: Path,
        runtime_action: str,
        *,
        default_branch: str = "main",
        preset: Literal["oss", "internal"] = "oss",
        force: bool = False,
    ) -> Self:
        """Create one understandable starter declaration."""
        configuration = _configuration_path(root)
        if configuration.exists() and not force:
            message = f"Refusing to replace existing declaration: {configuration}"
            raise FileExistsError(message)
        provider = load_provider("github")
        source = _starter_configuration(runtime_action, default_branch, preset)
        graph = Graph.from_yaml(source)
        provider.generate(graph)
        root.mkdir(parents=True, exist_ok=True)
        configuration.write_text(source)
        return cls(root, graph, provider)


def _configuration_path(root: Path) -> Path:
    configuration = root / CONFIGURATION_PATH
    legacy = root / LEGACY_CONFIGURATION_PATH
    if legacy.exists() or legacy.is_symlink():
        if configuration.exists():
            message = (
                f"Both {CONFIGURATION_PATH} and {LEGACY_CONFIGURATION_PATH} exist; "
                f"remove {LEGACY_CONFIGURATION_PATH}"
            )
            raise FileExistsError(message)
        message = (
            f"{LEGACY_CONFIGURATION_PATH} is no longer supported; "
            f"rename it with `git mv {LEGACY_CONFIGURATION_PATH} {CONFIGURATION_PATH}`"
        )
        raise FileNotFoundError(message)
    return configuration


def _starter_configuration(
    runtime_action: str,
    default_branch: str,
    preset: Literal["oss", "internal"],
) -> str:
    runner = "ubuntu-latest" if preset == "oss" else "self-hosted"
    return f"""version: 0
provider:
  name: github
  configuration:
    default-branch: {json.dumps(default_branch)}
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
