# Upgrade to v0.1.8

Quality Graph `v0.1.8` intentionally breaks compatibility with the former specification filename
and adds explicit synchronization of GitHub merge requirements. Upgrade the CLI, Action runtime,
source declaration, and generated workflows as one reviewed change.

## What changed

### Breaking: `qg.yaml` is the only specification

The human-authored source file was renamed from `quality-graph.yml` to `qg.yaml`. There is no
fallback, alias, precedence rule, or deprecation window:

- a repository containing only `quality-graph.yml` receives a migration error;
- a repository containing both filenames is rejected;
- `qg init`, `qg generate`, `qg validate`, project discovery, and trusted GitHub runtime reads use
  only `qg.yaml`;
- generated workflow filenames remain unchanged, including
  `.github/workflows/quality-graph.yml`.

### Required checks are declarative

The GitHub provider accepts an optional merge requirement:

```yaml
provider:
  name: github
  configuration:
    merge:
      required: true
```

The manual `qg github required-checks sync` command synchronizes the stable aggregate
`Quality Graph` context through classic branch protection or a repository ruleset. Generation and
workflow execution never mutate repository settings. Synchronization preserves unrelated checks,
GitHub App bindings, review requirements, restrictions, bypass actors, and supported protection
flags; an unchanged configuration performs no write.

### Dashboard publication is recoverable

The trusted watcher can finalize terminal workflow runs from validated artifacts, while completed
events remain an idempotent repair path. Live and final publication update one stable synthetic
check, stale watchers cannot overwrite newer runs, topology changes do not leave watchers waiting
for removed nodes, and workflow-run discovery follows GitHub pagination.

### Exit-code diagnostics and node approvals

An exit-code result may capture UTF-8 command output for the Job Summary and dashboard:

```yaml
run: |
  mkdir -p reports
  set -o pipefail
  make check 2>&1 | tee reports/check.log
results:
  exit-code: reports/check.log
policy:
  approvals:
    node: true
```

When node approval is enabled, Quality Graph renders the corresponding administrator controls.
Invalid captured-output encoding is reported as an adapter failure.

## Ordered migration

### 1. Rename the source file

Do this before invoking the `v0.1.8` CLI:

```bash
git mv quality-graph.yml qg.yaml
```

Do not keep a compatibility copy or symlink under the old name.

### 2. Upgrade every installed package together

For repository dependencies:

```bash
uv add --dev quality-graph-cli==0.1.8 quality-graph-github==0.1.8
```

Add `quality-graph-python==0.1.8` when the repository uses the optional Python gates. If the
repository depends directly on `quality-graph-core`, upgrade it to `0.1.8` as well.

For an isolated user-level installation:

```bash
uv tool install --force quality-graph-cli==0.1.8 --with quality-graph-github==0.1.8
```

Do not mix package versions.

### 3. Update immutable Action pins

Update both trusted and untrusted runtime pins in `qg.yaml`:

```yaml
provider:
  name: github
  configuration:
    runtime:
      action: alchemmist/quality-graph@b947f26e97ccf1c755050dfe38d98cbb688edb69
      publisher-action: alchemmist/quality-graph@b947f26e97ccf1c755050dfe38d98cbb688edb69
```

The SHA is the immutable commit attached to `v0.1.8`. Do not replace it with `main`, `v0`, or
another moving reference. Updating only the CLI is insufficient: an older publisher runtime still
looks for the former filename and cannot publish the dashboard after the rename.

### 4. Regenerate and validate

```bash
qg generate
qg validate
```

Review and commit the complete source/output set:

```bash
git add qg.yaml .github/workflows .quality-graph/manifest.json .prettierignore
```

The generated `.github/workflows/quality-graph*.yml` filenames are expected and must not be renamed.

### 5. Verify the pipeline before changing protection

Open a small pull request and verify:

- every expected native job runs;
- the managed dashboard reaches a terminal state;
- the synthetic `Quality Graph` check matches the dashboard;
- reruns and skipped downstream jobs remain visible;
- no required check still references a removed per-job context.

### 6. Optionally synchronize merge requirements

Set `merge.required` to the desired value, then run the administrative command explicitly:

```bash
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=token qg github required-checks sync
```

Inspect the printed plan before the mutation result. The token needs repository
`Administration: write`. Organization-owned rulesets require organization-level Administration
and are not modified by the repository-scoped command. If classic protection and a repository
ruleset both own required checks, synchronization fails as ambiguous rather than partially updating
one surface.

## Troubleshooting

### The CLI reports `quality-graph.yml is no longer supported`

Rename the file to `qg.yaml`. If both names exist, remove the old entry; do not configure a
fallback.

### Jobs run but no dashboard appears

Confirm that `runtime.publisher-action` points to the `v0.1.8` SHA above and regenerate the
publisher workflow. The publisher workflow runs trusted code from the default branch, so a
publisher-pin change proposed only inside a pull request becomes active after it reaches that
branch.

### Required-check synchronization is rejected

Check the reported ownership. Repository rulesets and classic protection are supported, but
organization-owned or ambiguous required-check configurations require an administrator to resolve
the owning surface first.

## Release details

- [v0.1.8 release notes](https://github.com/alchemmist/quality-graph/releases/tag/v0.1.8)
- [Full changelog](https://github.com/alchemmist/quality-graph/compare/v0.1.7...v0.1.8)
