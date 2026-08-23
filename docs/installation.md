# Installation and generation

Install the CLI and GitHub provider from the same exact release. Pin the generated runtime
Action to the commit attached to that release, never to a mutable branch or major-version tag.

```bash
export QG_SHA=<40-character-commit-shown-on-the-v0.1.0-release>
uv tool install quality-graph-cli==0.1.1 --with quality-graph-github==0.1.1
qg init --root ../project \
  --runtime-action "alchemmist/quality-graph@$QG_SHA"
```

`qg init` creates `quality-graph.yml`. Use `--preset internal` only to select a self-hosted
starter runner; it does not grant credentials or write permissions. Existing declarations
are preserved unless `--force` is explicit.

Generate and commit observable GitHub files:

```bash
qg generate --root ../project
(cd ../project && git add quality-graph.yml .github/workflows .quality-graph/manifest.json)
```

`qg validate` recomputes output in memory and fails for an invalid declaration, a missing
generated file, or stale generated content. It never changes the repository.

The generated files are:

- `.github/workflows/quality-graph.yml`: untrusted native graph execution;
- `.github/workflows/quality-graph-publish.yml`: trusted publication and commands;
- `.quality-graph/manifest.json`: expanded semantic graph and digest.

Review and update both the installed CLI commit and
`provider.configuration.runtime.action` together.

The workspace builds four distributions independently. Installing `quality-graph-cli` without a
provider is supported, but provider-backed project commands fail with an actionable installation
message.
`quality-graph-python` is optional and can be installed separately for reusable Python quality
gates.
