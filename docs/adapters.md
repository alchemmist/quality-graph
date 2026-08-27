# Result adapters

All adapters produce the same Result Protocol and reporting path.

## Exit code

Omit `results` to map a successful command to `passed` and any other outcome to a distinct
command failure. No report file is required.

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
