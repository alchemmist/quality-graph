#!/usr/bin/env bash
set -euo pipefail

mkdir -p reports
uv run ruff check --output-format sarif --output-file reports/ruff.sarif .
