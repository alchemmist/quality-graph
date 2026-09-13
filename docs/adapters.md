# Result adapters

All adapters produce the same Result Protocol and reporting path.

## Exit code

Omit `results` to map a successful command to `passed` and any other outcome to a distinct
command failure. No report file is required.

An exit-code adapter may optionally read captured UTF-8 command output:

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

The captured output becomes bounded diagnostic detail rendered as text. Enabling node approval adds
the corresponding `/qg ignore <node>` and `/qg remove-ignore <node>` controls to the Job Summary
and managed dashboard.

## Native JSON

```yaml
results:
  native: reports/result.json
```

Identity and provenance must match the declared node and current workflow attempt.

Native producers own domain data: custom `summary` Markdown, metrics, findings, annotations,
diagnostics, and notes. They must not construct the complete Job Summary, stable finding labels, or
`/qg` commands. Quality Graph replaces report-supplied controls with semantic controls derived from
the findings and compiled node approval policy, then composes the provider presentation.

## SARIF

```yaml
results:
  sarif: reports/lint.sarif
```

SARIF levels map to notice, warning, and error. The adapter reads rule IDs, text or Markdown
messages, partial fingerprints, and the first physical source location.

## JUnit XML

```yaml
results:
  junit: reports/tests.xml
```

Both `testsuite` and `testsuites` roots are accepted. Failures and errors become stable
findings; skipped and total counts become metrics. XML is parsed through `defusedxml`.

Reports must exist inside the repository workspace and remain below 10 MiB. Missing,
malformed, oversized, and traversal reports create adapter failures rather than rewriting the
underlying command outcome.

## Producer data contract

For a custom check, use `results.native` with **producer report v0**. The input schema is
[`producer-v0.schema.json`](https://github.com/alchemmist/quality-graph/blob/main/schemas/producer-v0.schema.json),
also available through `qg result schema --producer`. This is a data-only input to collection;
the published artifact remains Result v0.

```json
{
  "reportVersion": 0,
  "status": "failed",
  "failureKind": "quality",
  "metrics": [{"label": "Files checked", "value": "5"}],
  "findings": [{
    "id": "invalid-setting",
    "severity": "error",
    "message": "The enabled setting must be true.",
    "group": "Configuration"
  }],
  "diagnostics": [{
    "kind": "command",
    "message": "Expected enabled=true",
    "detail": "Observed enabled=false"
  }]
}
```

`reportVersion` and terminal `status` are required; `failureKind` is required exactly for failed
or cancelled results. Metrics, findings, annotations, diagnostics, notes and optional summary
content use the existing Result v0 field definitions and limits. Finding IDs must be stable and
unique within the report. Unknown fields, unsupported versions, nonterminal states, invalid
locations and inconsistent outcomes fail validation. A passing report cannot hide a failed command.

Producers must omit `nodeId`, `title`, `provenance`, `controls` and `schemaVersion`. Collection
supplies identity and provenance from its execution context, derives controls from graph policy,
and validates the resulting artifact. Supplying these fields in a producer report is an error.
Complete native Result v0 input remains supported with its existing exact provenance checks.

Quality Graph builds the headings, metric tables, finding groups, diagnostic code blocks, source
locations, bounds and administrator controls. Producers supply values and messages, not table
layout, status banners or `/qg` commands. Optional custom summary content remains available for
compatibility; it is not needed to produce a complete report.

A runnable custom producer is
[`check_settings.py`](https://github.com/alchemmist/quality-graph/blob/main/examples/producers/check_settings.py):

```yaml
run: python examples/producers/check_settings.py settings.json --output reports/settings.json
results:
  native: reports/settings.json
```

JUnit and SARIF are standard producer formats converted through the same result and rendering
path. JUnit findings include the test name, and diagnostics preserve bounded failure/error traces.
For commands without a structured format, captured output is diagnostic text; the framework does
not infer source findings or test counts from arbitrary stdout.
