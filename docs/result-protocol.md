# Result protocol

Every completed node publishes one deterministic Quality Graph Result. The provisional JSON
Schema is [`schemas/result-v0.schema.json`](../schemas/result-v0.schema.json).

Required result data includes:

- `schemaVersion`, `nodeId`, `title`, `status`, and optional `failureKind`;
- producer-owned Markdown `summary` body and ordered string metrics;
- stable findings and safe repository-relative source annotations;
- structured diagnostics, semantic approval controls, and notes;
- repository, pull request, head SHA, run, attempt, and graph-digest provenance.

`failureKind` distinguishes quality, command, adapter, protocol, cancellation, and
infrastructure failures. Approvals affect only quality failures. Adapter and command failures
cannot be hidden through finding approval.

Finding IDs must represent semantic identity rather than line position. SARIF uses partial
fingerprints when available; JUnit uses suite, class, test, failure type, and message. Moving
an unchanged finding within a file therefore does not automatically invalidate approval.

Any language can produce conforming JSON. Validate it without importing Python:

```bash
qg result validate result.json
```

Emit a minimal result through the CLI with `qg result emit`; workflow provenance arguments
are mandatory. Serialization is UTF-8, sorted, indented JSON with one final newline.

The protocol rejects unknown fields, duplicate finding IDs, absolute or traversal paths,
invalid line ranges, oversized collections, and inconsistent status/failure combinations.

`summary` is custom body content, not a complete GitHub Job Summary. Producers may use it for
explanatory Markdown, custom tables, metrics context, and domain-specific notes. The GitHub
provider owns the result anchor, status and title, standard metrics, findings with stable IDs and
source locations, diagnostics, notes, size bounds, and administrator controls.

Controls in collected artifacts are framework-owned derived state. The execution runtime replaces
any controls supplied by a native report with finding-, file-, and node-level controls allowed by
the compiled graph policy. Their checkbox markers and canonical apply and reverse `/qg` commands
are rendered centrally; producer Markdown is never an authorization source.
