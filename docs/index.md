Quality Graph turns repository-owned checks into a native GitHub Actions pipeline.
You describe commands, environments, and dependencies in `quality-graph.yml`; Quality Graph
generates the workflows and connects their results to pull-request reporting and governance.

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

nodes:
  lint:
    run: make lint

  test:
    needs: [lint]
    run: make test
    results:
      junit: reports/tests.xml
```

Each node remains an ordinary command or reusable action. `needs` defines execution order, and
`results` lets Quality Graph render structured output such as test failures.

Generate the GitHub workflows from the declaration:

```bash
qg generate
```

Generated workflows keep native jobs, logs, statuses, and retries. The declaration remains the
source of truth.

[Get started →](quickstart.md)
