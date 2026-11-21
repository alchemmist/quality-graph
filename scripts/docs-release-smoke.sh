#!/usr/bin/env bash
set -euo pipefail

root=$(pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
python3 - "$root" <<'PY'
import html
import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
release = tomllib.loads((root / 'docs-release.toml').read_text())

def block(page, label, language):
    page = (root / 'site' / page / 'index.html').read_text()
    marker = f'<!-- docs-smoke: {label} -->'
    if page.count(marker) != 1:
        raise ValueError(f'Expected one {label} marker')
    match = re.match(rf'\s*<pre><code class="language-{language}">(.*?)</code></pre>',
                     page.split(marker)[1], re.S)
    if not match:
        raise ValueError(f'Expected a {language} example immediately after {label}')
    return html.unescape(match[1])

install = block('quickstart', 'install', 'bash').strip().removeprefix('uv add --dev ').split()
assert install == [f"quality-graph-{pkg}=={release['version']}" for pkg in ('cli', 'github')]
Path('release-tag').write_text('v' + release['version'])
Path('release-commit').write_text(release['action_sha'])
Path('packages.txt').write_text('\n'.join(install))
for page in ('quickstart', 'flows'):
    declaration = block(page, 'declaration', 'yaml')
    assert f"action: alchemmist/quality-graph@{release['action_sha']}\n" in declaration
    Path(f'{page}.yaml').write_text(declaration)
Path('producer.json').write_text(block('adapters', 'producer', 'json'))
PY
tag=$(cat release-tag)
refs=$(git ls-remote --tags https://github.com/alchemmist/quality-graph.git "refs/tags/$tag" "refs/tags/$tag^{}")
commit=$(awk -v tag="refs/tags/$tag" '$2 == tag {direct=$1} $2 == tag "^{}" {peeled=$1} END {print peeled ? peeled : direct}' <<<"$refs")
if [[ -z $commit ]]; then
	echo "Release tag $tag not found" >&2
	exit 1
fi
if [[ $commit != "$(cat release-commit)" ]]; then
	echo "Documented runtime does not match release tag $tag" >&2
	exit 1
fi
uv venv --python '>=3.12' .venv
uv pip install --python .venv/bin/python -r packages.txt
for example in quickstart flows; do
	cp "$example.yaml" qg.yaml
	.venv/bin/qg generate
	.venv/bin/qg validate
done
.venv/bin/qg result schema --producer >producer-schema.json
uv run --isolated --no-project --with jsonschema python -c 'import json, jsonschema; from pathlib import Path; jsonschema.validate(json.loads(Path("producer.json").read_text()), json.loads(Path("producer-schema.json").read_text()))'
