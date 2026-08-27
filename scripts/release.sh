#!/usr/bin/env bash
set -euo pipefail

kind=${1:-}
case "$kind" in
patch | minor | major) ;;
*)
	echo "Usage: $0 patch|minor|major" >&2
	exit 2
	;;
esac

root=$(git rev-parse --show-toplevel)
cd "$root"

if [[ $(git branch --show-current) != main ]]; then
	echo "Release must run from the main branch." >&2
	exit 1
fi
if [[ -n $(git status --porcelain) ]]; then
	echo "Release requires a clean working tree." >&2
	exit 1
fi

git fetch origin main --tags
if [[ $(git rev-parse HEAD) != $(git rev-parse origin/main) ]]; then
	echo "Local main must exactly match origin/main." >&2
	exit 1
fi

packages=(quality-graph-core quality-graph-python quality-graph-github quality-graph-cli)
current=$(uv version --package "${packages[0]}" --short)
for package in "${packages[@]:1}"; do
	if [[ $(uv version --package "$package" --short) != "$current" ]]; then
		echo "All workspace package versions must match." >&2
		exit 1
	fi
done

next=$(uv version --bump "$kind" --package "${packages[0]}" --dry-run --short)
tag="v$next"
if git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null; then
	echo "Tag already exists: $tag" >&2
	exit 1
fi

for package in "${packages[@]}"; do
	uv version "$next" --package "$package" --frozen
done
dependency_files=(apps/qg/pyproject.toml packages/github/pyproject.toml)
sed -i.bak "s/quality-graph-core==$current/quality-graph-core==$next/g" \
	"${dependency_files[@]}"
rm -f "${dependency_files[@]/%/.bak}"
uv lock

make check BASE=origin/main

git add apps/qg/pyproject.toml packages/core/pyproject.toml \
	packages/github/pyproject.toml packages/python/pyproject.toml uv.lock
git commit -m "release $tag"
git tag -a "$tag" -m "release $tag"
git push --atomic origin main "$tag"
