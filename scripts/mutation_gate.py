"""Enforce the repository mutation score policy."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TypedDict, cast


class MutationStats(TypedDict):
    """Describe verdict counts exported by mutmut."""

    killed: int
    survived: int
    suspicious: int
    timeout: int


def mutation_score(stats: MutationStats) -> float:
    """Calculate the score over mutants with a definitive gate verdict."""
    total = stats["killed"] + stats["survived"] + stats["suspicious"] + stats["timeout"]
    return 1.0 if total == 0 else stats["killed"] / total


def main() -> None:
    """Read exported statistics and fail when the policy threshold is missed."""
    parser = argparse.ArgumentParser()
    parser.add_argument("stats", type=Path)
    parser.add_argument("--threshold", type=float, default=0.9)
    arguments = parser.parse_args()
    stats = cast("MutationStats", json.loads(arguments.stats.read_text()))
    score = mutation_score(stats)
    sys.stdout.write(f"mutation score: {score:.2%} (required: {arguments.threshold:.2%})\n")
    if score < arguments.threshold:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
