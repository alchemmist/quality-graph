# Quality Graph

Bring your checks. Quality Graph provides the graph, result protocol, reporting, and
governance.

Quality Graph turns a small repository-owned declaration into native GitHub Actions jobs
and a complete pull-request quality experience. Checks remain ordinary commands and
reusable actions; the framework owns result artifacts, Job Summaries, annotations,
dashboards, labels, reruns, and authenticated approvals.

```yaml
version: 0
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
uvx --from 'git+https://github.com/alchemmist/quality-graph@<sha>' \
  qg init --runtime-action 'alchemmist/quality-graph@<sha>'
uvx --from 'git+https://github.com/alchemmist/quality-graph@<sha>' qg generate
git add quality-graph.yml .github/workflows .quality-graph/manifest.json
```

Generated workflows preserve independent runners, native dependencies, logs, summaries,
statuses, and retries. Pull-request code receives no secrets or write token. A separate
default-branch `workflow_run` publisher treats artifacts as untrusted data before updating
GitHub state.

The project is a functional pre-release. Configuration and result protocol version `0` may
change without migration tooling. Do not use a mutable Action ref.

## Documentation

- [Installation and generation](docs/installation.md)
- [Configuration reference](docs/configuration.md)
- [Result protocol](docs/result-protocol.md)
- [Result adapters](docs/adapters.md)
- [Permissions and fork security](docs/security.md)
- [Administrator commands](docs/commands.md)
- [Compatibility policy](docs/compatibility.md)
- [Release preparation](docs/release.md)
- [Contributing](CONTRIBUTING.md)

Complete [Python](examples/python), [TypeScript](examples/typescript), and
[Go](examples/go) fixtures compile through the same public interfaces.

The product specification and completion criteria are in [TASK.md](TASK.md).
