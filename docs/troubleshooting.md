# Troubleshooting

## Provider is not installed

Provider-backed commands require the CLI and provider in the same environment:

```bash
uv add --dev quality-graph-cli==0.1.2 quality-graph-github==0.1.2
uv run qg validate
```

With a user-level tool, install both distributions into that tool environment:

```bash
uv tool install quality-graph-cli==0.1.2 --with quality-graph-github==0.1.2
```

Do not install mismatched versions.

## Generated file is missing or stale

`qg validate` never writes files. Regenerate, review, and commit the result:

```bash
uv run qg generate
uv run qg validate
git diff -- quality-graph.yml .github/workflows .quality-graph
```

Do not edit generated workflows or `.quality-graph/manifest.json` by hand.

## Runtime Action ref is rejected

`provider.configuration.runtime.action` requires an exact 40-character commit SHA. For release
`v0.1.2`, use:

```yaml
action: alchemmist/quality-graph@a4a65abfc9364da6801be56b992358d302c7ad77
```

Update the CLI, provider, and runtime SHA as one reviewed change.

## Diff gate cannot find `origin/main`

Diff-based Python gates default to `origin/main`. Fetch history in CI:

```yaml
- uses: actions/checkout@v7
  with:
    persist-credentials: "false"
    fetch-depth: "0"
```

Or pass the intended base explicitly:

```bash
uv run qg-python-suppressions --base origin/trunk
```

## First graph-changing pull request has an incomplete dashboard

The trusted publisher currently reads topology from the base branch. New or renamed nodes in the
pull-request head cannot become trusted dashboard topology in the same pull request. Merge the
reviewed bootstrap and open a probe pull request from the updated base.
[Issue #23](https://github.com/alchemmist/quality-graph/issues/23) tracks secure single-PR graph
evolution.

## Structured report is missing or malformed

The command and adapter are separate failure sources. Confirm that the node command writes the
declared file below the repository workspace and that it is smaller than 10 MiB. Use no `results`
field when only process exit status should determine the node result.

## `qg init` refuses to replace a declaration

Initialization preserves an existing `quality-graph.yml`. Edit it directly, or use `--force` only
when replacement is intentional and the current file is safely committed.
