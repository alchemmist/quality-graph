# Quality Graph GitHub Provider

GitHub workflow generation, trusted publication, and Action runtime for Quality Graph.

The distribution registers `github` in the `qg.providers` entry-point group.
`quality-graph-core` is its only Quality Graph workspace dependency; third-party runtime
dependencies remain private implementation details. The provider never imports the CLI.
