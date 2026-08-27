# Result protocol

Every completed node publishes one deterministic Quality Graph Result. The provisional JSON
Schema is [`schemas/result-v0.schema.json`](https://github.com/alchemmist/quality-graph/blob/main/schemas/result-v0.schema.json).

Required result data includes:

- `schemaVersion`, `nodeId`, `title`, `status`, and optional `failureKind`;
- Markdown `summary` and ordered string metrics;
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
