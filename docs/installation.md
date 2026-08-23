# Installation and generation

Quality Graph is not published to PyPI yet. Install the pre-release from an exact Git
commit and use the same commit for the generated runtime Action.

```bash
export QG_SHA=<reviewed-40-character-commit>
uvx --from "git+https://github.com/alchemmist/quality-graph@$QG_SHA" \
  qg init --runtime-action "alchemmist/quality-graph@$QG_SHA"
```

`qg init` creates `quality-graph.yml`. Use `--preset internal` only to select a self-hosted
starter runner; it does not grant credentials or write permissions. Existing declarations
are preserved unless `--force` is explicit.

Generate and commit observable GitHub files:

```bash
uvx --from "git+https://github.com/alchemmist/quality-graph@$QG_SHA" qg generate
git add quality-graph.yml .github/workflows .quality-graph/manifest.json
```

`qg validate` recomputes output in memory and fails for an invalid declaration, a missing
generated file, or stale generated content. It never changes the repository.

The generated files are:

- `.github/workflows/quality-graph.yml`: untrusted native graph execution;
- `.github/workflows/quality-graph-publish.yml`: trusted publication and commands;
- `.quality-graph/manifest.json`: expanded semantic graph and digest.

Review and update both the installed CLI commit and `runtime.action` together.
