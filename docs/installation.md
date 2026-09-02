# Installation and generation

Install the CLI and GitHub provider from the same exact release. Pin the generated runtime
Action to the commit attached to that release, never to a mutable branch or major-version tag.

## Repository-pinned installation

Use repository dev dependencies when every contributor and CI job should receive the same
Quality Graph version through the existing lockfile:

```bash
uv add --dev quality-graph-cli==0.1.2 quality-graph-github==0.1.2
uv run qg init \
  --default-branch main \
  --runtime-action alchemmist/quality-graph@a4a65abfc9364da6801be56b992358d302c7ad77
```

Add `quality-graph-python==0.1.2` to the same command only when the repository uses the optional
Python gates.

## User-level tool installation

Use an isolated uv tool when operating on several repositories without adding the CLI itself to
their dependencies:

```bash
uv tool install quality-graph-cli==0.1.2 --with quality-graph-github==0.1.2
qg --version
```

The repository-pinned form is preferable for CI and shared development. The user-level form is
convenient for evaluation and administration. Do not mix CLI and provider versions.

## Initialize and generate

`qg init` creates `qg.yaml`. `--default-branch` records the repository branch that
receives pull requests and trusted pushes; it defaults to `main` and never queries GitHub. Use
`--preset internal` only to select a self-hosted starter runner; it does not grant credentials or
write permissions. Existing declarations are preserved unless `--force` is explicit.

Generate and commit observable GitHub files:

```bash
uv run qg generate
uv run qg validate
git add qg.yaml .github/workflows .quality-graph/manifest.json .prettierignore
```

`qg validate` recomputes output in memory and fails for an invalid declaration, a missing
generated file, or stale generated content. It never changes the repository.

The generated files are:

- `.github/workflows/quality-graph.yml`: untrusted native graph execution;
- `.github/workflows/quality-graph-publish.yml`: trusted publication and commands;
- `.quality-graph/manifest.json`: expanded semantic graph and digest.

List the provider-owned paths without duplicating them in repository tooling:

```bash
uv run qg generated-files
```

`qg generate` maintains a marked block for these paths in `.prettierignore`. It preserves all
unrelated rules and replaces only its own block, so repeated generation is idempotent. Generated
workflows use standard yamllint sequence indentation. The supported formatter contract is
Prettier 3.6.2 with its default configuration and yamllint 1.37 or newer with line-length policy
chosen by the adopting repository.

Review and update both the installed CLI commit and
`provider.configuration.runtime.action` together.

The workspace builds four distributions independently. Installing `quality-graph-cli` without a
provider is supported, but provider-backed project commands fail with an actionable installation
message.
`quality-graph-python` is optional and can be installed separately for reusable Python quality
gates.

Continue with the [quickstart](quickstart.md), or follow the staged
[migration guide](migration.md) for an existing CI repository.
