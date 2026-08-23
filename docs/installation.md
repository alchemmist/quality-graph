# Installation and generation

Quality Graph is not published to PyPI yet. Check out an exact Git commit and use its locked
uv workspace with the same commit for the generated runtime Action.

```bash
export QG_SHA=<reviewed-40-character-commit>
git clone https://github.com/alchemmist/quality-graph.git
cd quality-graph
git checkout "$QG_SHA"
uv run --all-packages qg init --root ../project \
  --runtime-action "alchemmist/quality-graph@$QG_SHA"
```

`qg init` creates `quality-graph.yml`. Use `--preset internal` only to select a self-hosted
starter runner; it does not grant credentials or write permissions. Existing declarations
are preserved unless `--force` is explicit.

Generate and commit observable GitHub files:

```bash
uv run --all-packages qg generate --root ../project
git add quality-graph.yml .github/workflows .quality-graph/manifest.json
```

`qg validate` recomputes output in memory and fails for an invalid declaration, a missing
generated file, or stale generated content. It never changes the repository.

The generated files are:

- `.github/workflows/quality-graph.yml`: untrusted native graph execution;
- `.github/workflows/quality-graph-publish.yml`: trusted publication and commands;
- `.quality-graph/manifest.json`: expanded semantic graph and digest.

Review and update both the installed CLI commit and `runtime.action` together.

The workspace builds three distributions independently. Once they are published, install
the CLI and provider together with `uv tool install qg --with qg-github`. Installing `qg`
without a provider is supported, but provider-backed project commands fail with an
actionable installation message.
