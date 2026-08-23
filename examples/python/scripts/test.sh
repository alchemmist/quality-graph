#!/usr/bin/env bash
set -euo pipefail

mkdir -p reports
uv run pytest --junitxml reports/pytest.xml
