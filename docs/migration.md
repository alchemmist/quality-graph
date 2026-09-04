# Migrating an existing repository

## Rename the source specification

Quality Graph accepts `qg.yaml` as its only source specification filename. Repositories using
the former filename must migrate explicitly:

```console
git mv quality-graph.yml qg.yaml
qg generate
qg validate
```

Commands reject the former filename instead of treating it as a fallback. If both files exist,
remove `quality-graph.yml` before running Quality Graph again.

This rename is a breaking change in `v0.1.8`. When upgrading an existing Quality Graph
installation, use the ordered [v0.1.8 upgrade guide](upgrading-v0.1.8.md) so the package version,
source filename, immutable Action pins, and generated workflows move together.

Migrate orchestration before deleting working checks. Existing commands remain the behavioral
baseline; Quality Graph initially calls the same Make targets and report producers.

## 1. Inventory the current pipeline

For every existing job, record:

- its command and setup requirements;
- dependencies on other jobs;
- required runner, container, services, and environment variables;
- report files such as SARIF or JUnit XML;
- permissions, secrets, artifacts, labels, and required-check status.

Separate portable quality checks from repository-specific deployment or credential-bearing jobs.
Quality Graph pull-request execution is secretless by default; do not move privileged deployment
steps into the graph.

## 2. Install without changing required checks

```bash
uv add --dev \
  quality-graph-cli==0.1.8 \
  quality-graph-github==0.1.8
```

Add `quality-graph-python==0.1.8` when reusing its optional Python gates. Keep the old workflows
enabled during migration.

## 3. Map jobs to graph nodes

Use one node per independently useful GitHub job. Preserve parallel branches and encode only real
dependencies in `needs`.

Use `events: [pull-request]` for diff-only checks. To reuse the remaining checks on the default
branch without their pull-request scheduling chain, configure `execution.push.dependencies: none`.

```yaml
nodes:
  lint:
    run: make lint
    results:
      sarif: reports/lint.sarif

  unit:
    needs: [lint]
    run: make t-fast
    results:
      junit: reports/unit.xml

  integration:
    needs: [lint]
    run: make t-medium
    results:
      junit: reports/integration.xml
```

Do not copy arbitrary workflow YAML into the declaration. Put repeated runner/setup behavior in a
profile and leave deployment, release, and credential-bearing workflows outside the graph.

## 4. Bootstrap generated files

```bash
uv run qg init \
  --default-branch trunk \
  --runtime-action alchemmist/quality-graph@b947f26e97ccf1c755050dfe38d98cbb688edb69
uv run qg generate
uv run qg validate
```

If a declaration was prepared manually, skip `init` and run `generate` directly.
Replace `trunk` with the repository's actual default branch. Quality Graph does not query GitHub
for this value.

Generation also adds a compiler-owned block to `.prettierignore`. Commit that change, then run the
repository formatter from its root. Existing ignore rules remain untouched. Repositories that
maintain other formatter exclusions can obtain the exact current paths with:

```bash
uv run qg generated-files
```

The bootstrap pull request may show an incomplete or failing aggregate dashboard because the
trusted publisher evaluates topology from the base branch. Review the generated workflows and
individual execution jobs, merge the bootstrap, then open a probe pull request from the updated
default branch.

## 5. Compare old and new results

Keep both systems active for at least one representative pull request. Compare:

- commands and exit codes;
- job dependencies and parallelism;
- findings, annotations, summaries, and report counts;
- fork behavior and permissions;
- required checks and branch protection.

Investigate differences before changing required-check policy. A green replacement is not proof
of parity if a command, report adapter, or changed-file base was silently omitted.

## 6. Switch governance

After the probe is green:

1. make the aggregate `Quality Graph` check required;
1. remove superseded old required checks;
1. delete only workflows and scripts whose behavior is now represented elsewhere;
1. retain repository-specific commands called by graph nodes;
1. run `uv run qg validate` after the cleanup.

Generated workflows are outputs, not customization points. Change `qg.yaml`, regenerate,
and commit source and outputs together.

## Monori-shaped repositories

A repository with a mature internal graph should migrate node-by-node rather than importing its
orchestrator implementation. Reuse proven Make targets, report producers, quality policies, and
Python gates. Replace internal dashboard, artifact, authorization, and workflow mechanics with the
public provider instead of running two lifecycle implementations permanently.
