Quality Graph connects repository-owned checks into a native CI pipeline and
provides a shared result protocol, pull-request reporting, and governance.

Release 0.1.2 supports event-specific graph projections: set node `events` to
`pull-request`, `push`, or both. Configure `provider.configuration.default-branch`
explicitly so generated workflow filters match the repository:

```yaml
provider:
  name: github
  configuration:
    default-branch: main
    runtime:
      action: alchemmist/quality-graph@<release-commit-sha>

nodes:
  lint:
    events: [pull-request, push]
    run: make lint
  diff-check:
    events: [pull-request]
    run: make diff-check
```

After changing the declaration, regenerate and validate the compiler-owned files:

```bash
qg generate
qg generated-files
qg validate
```

`qg generated-files` emits one path per line for scripts, formatters, and staging commands.

[Read the documentation →](quickstart.md)
