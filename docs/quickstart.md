# Quickstart

This guide creates a native GitHub quality pipeline around commands already owned by a Python
repository. Quality Graph orchestrates and reports checks; it does not replace the tools behind
`make lint` or `make test`.

## 1. Install the released toolchain

```bash
uv add --dev quality-graph-cli==0.1.2 quality-graph-github==0.1.2
```

The CLI and provider are released together and must use the same exact version.

## 2. Create the declaration

```bash
uv run qg init \
  --default-branch main \
  --runtime-action alchemmist/quality-graph@a4a65abfc9364da6801be56b992358d302c7ad77
```

The 40-character ref binds runtime code to the reviewed `v0.1.2` release commit. Do not replace it
with `main`, `v0`, or another moving ref.

Replace the starter node with repository commands:

```yaml
version: 0
provider:
  name: github
  configuration:
    default-branch: main
    runtime:
      action: alchemmist/quality-graph@a4a65abfc9364da6801be56b992358d302c7ad77

profiles:
  default:
    runner: ubuntu-latest
    setup:
      - uses: actions/checkout@v7
        with:
          persist-credentials: "false"
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --locked

nodes:
  lint:
    title: Lint
    run: make lint

  test:
    title: Tests
    needs: [lint]
    run: make test
    results:
      junit: reports/pytest.xml
```

The test command must create the declared report. Omit `results` when exit status alone is enough.

## 3. Generate and inspect

```bash
uv run qg generate
uv run qg validate
git diff -- qg.yaml .github/workflows .quality-graph/manifest.json
```

Generation writes two workflows and one expanded manifest. Review commands, third-party Actions,
permissions, dependencies, and the exact runtime SHA before committing.

## 4. Commit the complete source/output set

```bash
git add pyproject.toml uv.lock qg.yaml .github/workflows .quality-graph
git commit -m "add quality graph"
```

CI should run `uv run qg validate` so declarations and generated files cannot drift.

## 5. Open the first pull request

The execution workflow runs repository code with read-only permissions and no secrets. A trusted
default-branch publisher validates result artifacts before updating the managed dashboard.

When the first pull request itself introduces or rewires graph nodes, the publisher still reads
the graph from the base branch. Merge the reviewed bootstrap, then open a small probe pull request
to validate the complete dashboard. This limitation is tracked in
[issue #23](https://github.com/alchemmist/quality-graph/issues/23).

For a repository that already has CI, follow the [migration guide](migration.md) instead of
removing old workflows immediately.
