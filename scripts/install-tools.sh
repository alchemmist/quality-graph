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
attempts=${QUALITY_GRAPH_TOOL_INSTALL_ATTEMPTS:-4}
retry_delay=${QUALITY_GRAPH_TOOL_RETRY_DELAY:-2}

install_tool() {
	module=$1
	attempt=1
	until GOBIN="$tools_bin" go install "$module"; do
		if [ "$attempt" -ge "$attempts" ]; then
			echo "Failed to install $module after $attempt attempts." >&2
			return 1
		fi
		delay=$((retry_delay * attempt))
		echo "Retrying $module in ${delay}s (attempt $((attempt + 1))/$attempts)." >&2
		sleep "$delay"
		attempt=$((attempt + 1))
	done
}

install_tool "github.com/rhysd/actionlint/cmd/actionlint@v${ACTIONLINT_VERSION}"
install_tool "mvdan.cc/sh/v3/cmd/shfmt@v${SHFMT_VERSION}"
install_tool "github.com/zricethezav/gitleaks/v8@v${GITLEAKS_VERSION}"
printf '%s' "$expected" >"$stamp"
