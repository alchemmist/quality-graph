# Release preparation

This repository prepares but does not trigger public releases during the pre-release pass.

A release candidate must pass:

- generated schema, workflow, manifest, and example freshness;
- Ruff ALL, strict mypy, static analysis, unit, fake-HTTP integration, and coverage gates;
- dependency and secret audits;
- mutation score threshold;
- deterministic sdist and wheel build, content inspection, clean installation, and CLI smoke;
- the repository's own generated Quality Graph workflow.

Before the first public release, resolve the contract-freeze and immutable distribution issues,
configure a protected `pypi` GitHub environment, and register a PyPI pending Trusted Publisher
for `alchemmist/quality-graph` and `.github/workflows/release.yml`.

The release workflow must use job-level `id-token: write`, build distributions in a separate
unprivileged job, publish only tag artifacts, and create attestations. No long-lived PyPI token
is required.

Do not create a tag, GitHub Release, moving major Action tag, or PyPI upload until the owner
explicitly starts the release task.
