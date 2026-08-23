#!/usr/bin/env bash
set -euo pipefail

ACTIONLINT_VERSION=1.7.7
SHFMT_VERSION=3.10.0
GITLEAKS_VERSION=8.21.2

repo_root=$(git rev-parse --show-toplevel)
tools_bin="${QUALITY_GRAPH_TOOLS_BIN:-$repo_root/.tools/bin}"
stamp="$tools_bin/versions"
expected=$(printf 'actionlint=%s\nshfmt=%s\ngitleaks=%s\n' \
	"$ACTIONLINT_VERSION" "$SHFMT_VERSION" "$GITLEAKS_VERSION")

if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$expected" ]; then
	exit 0
fi

mkdir -p "$tools_bin"
GOBIN="$tools_bin" go install "github.com/rhysd/actionlint/cmd/actionlint@v${ACTIONLINT_VERSION}"
GOBIN="$tools_bin" go install "mvdan.cc/sh/v3/cmd/shfmt@v${SHFMT_VERSION}"
GOBIN="$tools_bin" go install "github.com/zricethezav/gitleaks/v8@v${GITLEAKS_VERSION}"
printf '%s' "$expected" >"$stamp"
