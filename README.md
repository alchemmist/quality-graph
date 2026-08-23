# Quality Graph

Bring your checks. Quality Graph provides the graph, result protocol, reporting, and
governance.

Quality Graph is a GitHub-native framework for declaring repository-specific quality
pipelines as a graph. It compiles that graph into native GitHub Actions jobs and gives
every pull request a consistent quality experience:

- live and final conversation dashboards;
- complete GitHub Job Summaries;
- diagnostics attached to source lines;
- configurable failure labels that clean themselves up after recovery;
- portable results from independent CI jobs;
- stable findings and reversible administrator approvals;
- safe reruns and read-only handling of fork pull requests.

Checks remain yours. A node can run any command or toolchain and report a plain exit
code, SARIF, JUnit XML, or the native Quality Graph result format.

```yaml
version: 1

nodes:
  lint:
    run: make lint
    results:
      sarif: reports/lint.sarif

  tests:
    needs: [lint]
    run: make test
    results:
      junit: reports/tests.xml
```

Quality Graph is in its initial design and extraction phase. The product specification
is in [TASK.md](TASK.md).

The framework is being built from the proven Quality Graph infrastructure originally
developed in Monori, generalized into a stack-independent public project.
