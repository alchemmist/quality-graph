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
and managed dashboard. Whole-node ignore skips admitted quality and command failures, not adapter failures.

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
A repository-relative testcase `file` attribute becomes a source location, enabling file
approvals. The location uses line 1 because JUnit producers do not share a line-number convention.
Absolute paths and parent traversal are rejected. At most 10,000 findings and 100
test traces are serialized; summary and metrics retain full failure counts, and notes
state how many findings or traces were omitted.

Reports must exist inside the repository workspace and remain below 10 MiB. Missing,
malformed, oversized, and traversal reports create adapter failures rather than rewriting the
underlying command outcome.

## Producer data contract

The data-only contract requires v0.2.0 or later. For a custom check, use `results.native` with **producer report v0**. The input schema is
[`producer-v0.schema.json`](https://github.com/alchemmist/quality-graph/blob/vQG_RELEASE_VERSION/schemas/producer-v0.schema.json),
also available through `qg result schema --producer`. This is a data-only input to collection;
the published artifact remains Result v0.

<!-- docs-smoke: producer -->

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

Reports with omitted JUnit failures carry `omittedFindings`. Finding and file approvals
cannot clear these unknown failures; an explicitly enabled whole-node approval is required.
Upgrade the collector and trusted publisher together before emitting this optional field.

`omittedFindings` is an optional nonnegative integer (default zero) describing findings
excluded from the report. It is evidence metadata, not a display note. Producers must retain
the full count when truncating; combining reports adds prior omissions and newly discarded
findings. A positive count prevents raw success and partial approvals from clearing the check.
Whole-node approval remains an explicit policy decision. Diagnostic and display limits do not
change the count. Complete reports preserve their existing wire representation.

### Migrating Python source gates

The gate option is not in PyPI releases through 0.2.2. Use the source pin in the
[Python package installation instructions](https://github.com/alchemmist/quality-graph/tree/main/packages/python),
or install a later release that explicitly includes `--output`. Upgrade both collector and trusted
publisher to at least v0.2.1 for `omittedFindings` support.

When replacing an annotated repository-local gate, use `native` rather than exit-code collection:

```yaml
run: qg-python-suppressions --base origin/main --output reports/suppressions.json
results:
  native: reports/suppressions.json
```

The same `--output` option works with `no-comments`, `object-annotations`, `time-bombs`, and
`triple-quotes`. Run from the repository root with enough Git history to find the merge base
with `HEAD` (for example, set `fetch-depth: "0"` on `actions/checkout`). The gate
writes a producer-v0 report; the collector binds node identity and workflow provenance and
publishes source annotations. Without `--output`, the gate still prints local diagnostics.

## Lua 5.4

To migrate a tool that writes `{"ok": false, "count": 3}`, remove the separate converter
command and point `input` at the original report. The existing command runs once. Its
collection step evaluates the chunk below with the UTF-8 report text in `input`:

```yaml
run: make check
results:
  lua:
    input: reports/tool.json
    code: |
      local tool = json.decode(input)
      local report = {reportVersion=0, status="passed",
                      metrics={{label="Checks", value=tostring(tool.count)}}}
      if not tool.ok then
          report.status = "failed"
          report.failureKind = "quality"
          report.findings = {{id="tool-check", severity="error", message="Check failed"}}
      end
      return report
```

Alternatively, save the exact chunk above as `adapters/tool.lua` and declare:

```yaml
run: make check
results:
  lua:
    input: reports/tool.json
    file: adapters/tool.lua
```

Exactly one of `code` and `file` is required. Regenerate after changing either source:
its contents participate in the execution digest. The publisher reads declarations as
inert data; only collection runs Lua. Both providers use the native `reportVersion: 0`
producer contract, including finding locations, annotations, metrics, diagnostics and notes.
Do not return node identity, title, provenance, schema version or approval controls. A passing
report cannot mask a failed command (command failure); a valid quality failure remains a
quality failure. Invalid conversions are adapter failures, not approvable findings.

JSON strings, booleans and numbers map to Lua primitives. Arrays use contiguous 1-based
integer keys; objects use string keys. Decoded empty collections retain their type. For new
collections `{}` is an object; `json.array()` creates an empty array. Use `json.null` for
JSON null (`nil` removes a field). Mixed/sparse keys, cycles and nonfinite numbers are invalid.

Use a POSIX runner with Python 3.12+ and a Lupa wheel (Linux and macOS). The core package
installs the embedded Lua runtime; no `lua` binary is needed. The fresh worker has no runner
environment or provider API. It exposes `pairs`, `ipairs`, `type`, `tonumber`, `tostring`,
`assert`, `error`, `table.insert`, string methods `match`, `sub`, `upper`, `lower`, and the
JSON helpers. It does not expose Python, process, file, network, module loading or debug APIs.
Paths are workspace-relative regular files: symlinks and parent traversal are rejected.
Source is limited to 64 KiB, input and serialized output to 1 MiB each, output nesting to
64 levels, Lua memory to 16 MiB, execution to 2 million instructions and the worker to
3 CPU seconds / 4 wall-clock seconds. Linux additionally caps worker address space at 256 MiB.
