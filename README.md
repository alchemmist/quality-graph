# Quality Graph

[![Quality Graph](https://github.com/alchemmist/quality-graph/actions/workflows/quality-graph.yml/badge.svg?branch=main)](https://github.com/alchemmist/quality-graph/actions/workflows/quality-graph.yml)

Bring your checks. Quality Graph provides the graph, result protocol, reporting, and
governance.

Quality Graph turns a small repository-owned declaration into native GitHub Actions jobs
and a complete pull-request quality experience. Checks remain ordinary commands and
reusable actions; the framework owns result artifacts, Job Summaries, annotations,
dashboards, labels, reruns, and authenticated approvals.

```yaml
version: 0
provider:
  name: github
  configuration:
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
    run: make lint
    results:
      sarif: reports/lint.sarif
  test:
    needs: [lint]
    run: make test
    results:
      junit: reports/tests.xml
```

```bash
uv tool install qg==0.1.0 --with quality-graph-github==0.1.0
qg init --runtime-action 'alchemmist/quality-graph@<release-commit-sha>'
qg generate
git add quality-graph.yml .github/workflows .quality-graph/manifest.json
```

Generated workflows preserve independent runners, native dependencies, logs, summaries,
statuses, and retries. Pull-request code receives no secrets or write token. A separate
default-branch `workflow_run` publisher treats artifacts as untrusted data before updating
GitHub state.

The project is a functional pre-release. Configuration and result protocol version `0` may
change without migration tooling. Do not use a mutable Action ref.

## Architecture

Quality Graph is a locked uv workspace with four independently buildable distributions:

- `quality-graph-core` owns the platform-independent graph, result protocol, policies,
  schemas, and provider interface;
- `quality-graph-github` implements GitHub workflow generation, transport, publication, and the
  composite Action runtime;
- `quality-graph-python` provides optional reusable quality gates for Python repositories;
- `qg` is the command-line composition root and discovers installed providers through the
  `qg.providers` entry-point group.

The intended installation pins the CLI and provider to one release. Future providers such as
`qg-gitlab` can implement the same core interface without changes to the CLI or imports from
the GitHub provider.

## Documentation

- [Installation and generation](docs/installation.md)
- [Configuration reference](docs/configuration.md)
- [Provider authoring](docs/provider-authoring.md)
- [Result protocol](docs/result-protocol.md)
- [Result adapters](docs/adapters.md)
- [Permissions and fork security](docs/security.md)
- [Administrator commands](docs/commands.md)
- [Compatibility policy](docs/compatibility.md)
- [Release preparation](docs/release.md)

Complete [Python](examples/python), [TypeScript](examples/typescript), and
[Go](examples/go) fixtures compile through the same public interfaces.
