<h2><img src="docs/assets/quality-graph-eye-inline.svg" width="72" alt="Quality Graph logo" align="absmiddle"> Quality Graph</h2>

[Docs](https://alchemmist.github.io/quality-graph/)

Quality Graph turns a small repository-owned declaration into native GitHub Actions
jobs and a complete pull-request quality experience. Checks remain ordinary commands
and reusable actions: the project adds dependency-aware orchestration without hiding
runners, logs, statuses, or retries.

Every node can publish results through a portable protocol. Quality Graph validates
those artifacts and turns them into Job Summaries, annotations, dashboards, labels,
reruns, and authenticated approvals. Untrusted pull-request code runs without secrets
or a write token; a separate default-branch publisher performs trusted GitHub updates.

```yaml
version: 0
provider:
  name: github
  configuration:
    default-branch: main
    runtime:
      action: alchemmist/quality-graph@<exact-commit-sha>

profiles:
  default:
    runner: ubuntu-latest
    setup:
      - uses: actions/checkout@v7
        with:
          persist-credentials: "false"

nodes:
  lint:
    events: [pull-request, push]
    run: make lint
    results:
      sarif: reports/lint.sarif

  test:
    events: [pull-request]
    needs: [lint]
    run: make test
    results:
      junit: reports/tests.xml
```

`default-branch` controls the generated pull-request and push filters. Nodes run for both
events unless `events` narrows them to `pull-request` or `push`.

```bash
uv tool install quality-graph-cli==0.1.2 --with quality-graph-github==0.1.2
qg init \
  --default-branch main \
  --runtime-action alchemmist/quality-graph@<release-commit-sha>
qg generate
qg generated-files
qg validate
```

`qg generated-files` prints the provider-owned paths that should be committed or passed to
repository tooling. See the [installation guide](docs/installation.md) for the complete setup.
