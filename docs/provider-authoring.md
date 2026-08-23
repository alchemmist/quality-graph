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
