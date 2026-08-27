# Compatibility policy

Quality Graph is a functional pre-release. Package version `0.1.2`, graph version `0`,
and result schema version `0` carry no backward-compatibility guarantee.

Until the separate contract-freeze decision:

- configuration may change without a migration command;
- native result producers must pin the same exact runtime commit as generated workflows;
- generated output may change between commits;
- mutable Action refs and broad version ranges are unsupported.

Generated artifacts are compiler-owned. Prettier 3.6.2 compatibility is provided through the
managed `.prettierignore` block, and generated YAML follows standard yamllint sequence
indentation. The JSON representation is deterministic, sorted, two-space-indented compiler output;
formatters must consume the generated-file list or the managed ignore block instead of rewriting
it.

The CLI, provider, generated workflows, and Action runtime must use one compatible release set.
Providers declare an exact core dependency, and generated workflows pin the Action runtime by
commit. During an unreleased source installation, update the workspace checkout and
`provider.configuration.runtime.action` together; mixing compiler generations intentionally fails
artifact provenance validation.

The stable release will assign explicit graph and result versions, document supported reading
windows, and define migrations before publication. Existing version numbers will not be
silently reinterpreted after that freeze.

Python 3.12 is the oldest supported interpreter. Development and package smoke checks cover
Python 3.12 through 3.14.
