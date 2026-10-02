# Provider authoring

A provider is an independently installed distribution that registers one implementation in the
`qg.providers` entry-point group. It depends on `quality-graph-core` and must not import the CLI or
another provider.

```toml
[project.entry-points."qg.providers"]
gitlab = "qg_gitlab:provider"
```

The loaded object satisfies `quality_graph_core.Provider`: it exposes a stable `name` and a
`generate(Graph) -> GeneratedProject` method. Provider-specific configuration is available through
`Graph.provider.values`; core validates its shape as data, while the provider owns its semantics and
fail-closed validation.

Generated files must be deterministic, repository-relative, and complete. A provider should test
its wheel independently, verify discovery from package metadata, and reject configuration for a
different provider name. Provider releases must declare a compatible core version and document any
generated-output migration.

Providers can declare `ProviderCapabilities` (supported events, presentations and execution modes)
and call its `validate(graph)` before generation. Core checks portable structure and identifiers;
capability support belongs to the provider. Custom string event and presentation identifiers do
not require editing core. Existing structured push and dispatch declarations remain supported.

Profiles leave omitted runners and permissions unspecified. A provider supplies `ProfileDefaults`
to `graph.expanded_profiles(defaults)`. GitHub retains `ubuntu-latest` and read-only contents;
GitLab uses its configured execution image and runner tags. Explicit empty permissions stay empty.
Code consuming the Python graph model should resolve provider defaults before using a profile.

A `GeneratedFile` can specify `overwrite_marker`. The CLI refuses to overwrite existing content
without that prefix, regardless of provider name. Path traversal and symlink checks still apply.
An omitted marker retains the previous unconditional generated-file overwrite behavior.

GitLab declarations remain valid, but removing implicit GitHub defaults changes their manifest
digest. Regenerate committed files and deploy matching collector and publisher versions together.
