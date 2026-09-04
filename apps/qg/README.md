# qg

Provider-driven command-line application for Quality Graph projects.

The CLI discovers providers through the `qg.providers` entry-point group. Install it together
with the GitHub provider using
`uv tool install quality-graph-cli==0.1.8 --with quality-graph-github==0.1.8`.

Initialize a repository with its default branch, generate the provider-owned files, and list
those paths for repository tooling:

```bash
qg init \
  --default-branch main \
  --runtime-action alchemmist/quality-graph@<release-commit-sha>
qg generate
qg generated-files
qg validate
```

The graph declaration can select the generated workflow projections per node:

```yaml
nodes:
  lint:
    events: [pull-request, push]
    run: make lint
  diff-check:
    events: [pull-request]
    run: make diff-check
```

Nodes without `events` run for both supported events. See the
[configuration reference](../../docs/configuration.md) for dependency projection rules.
