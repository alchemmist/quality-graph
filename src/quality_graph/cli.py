"""Command-line interface for Quality Graph."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from quality_graph import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence


def parser() -> argparse.ArgumentParser:
    """Build the public command-line parser."""
    result = argparse.ArgumentParser(prog="qg", description="Quality Graph")
    result.add_argument("--version", action="version", version=__version__)
    return result


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the Quality Graph command-line interface."""
    parser().parse_args(arguments)
    return 0
