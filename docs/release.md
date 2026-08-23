# Releases

An exact semantic-version tag triggers the release workflow. The tag version must match all
four workspace distributions.

A release candidate must pass:

- generated schema, workflow, manifest, and example freshness;
- Ruff ALL, strict mypy, static analysis, unit, fake-HTTP integration, and coverage gates;
- dependency and secret audits;
- mutation score threshold;
- deterministic sdist and wheel builds for `quality-graph-core`, `qg-python`, `qg-github`, and `qg`,
  content inspection, clean installation, provider discovery, and provider-free CLI smoke;
- the repository's own generated Quality Graph workflow.

The repository uses tag-restricted GitHub environments and one PyPI pending Trusted Publisher
for each workspace distribution. Every publisher is bound to `alchemmist/quality-graph` and
`.github/workflows/release.yml`. The environments are `pypi` for `quality-graph-core`,
`pypi-python` for `qg-python`, `pypi-github` for `qg-github`, and `pypi-cli` for `qg`.

Run `make release-setup` once to configure those environments and the initial pending publishers
through the GitHub and PyPI interfaces. The setup uses no API token or repository secret.

PyPI allows at most three pending publishers per account and rejects duplicate pending OIDC
identities. For the first release, register `quality-graph-core`, `qg-python`, and `qg-github`
with their distinct environments before creating the tag. The first tag run publishes those
projects while `qg` remains failed and the GitHub Release remains blocked. Register `qg` with
`pypi-cli` after the three pending records convert to ordinary publishers, then rerun only failed
jobs in the same workflow run. The CLI publishes and the existing release job creates the GitHub
Release from all eight distribution files. Later releases complete in one pass.

The release workflow builds and verifies distributions in an unprivileged job. Four isolated
publication jobs receive only `id-token: write`, and PyPI creates attestations through Trusted
Publishing. A final job creates the GitHub Release from the exact uploaded files. No long-lived
PyPI token is used.

Release procedure:

1. merge a version PR with every local and pull-request check green;
1. confirm all four Trusted Publishers and the `pypi` environment are configured;
1. create the immutable `vX.Y.Z` tag at the reviewed release commit;
1. wait for all PyPI publication jobs and the GitHub Release job;
1. install the exact versions from PyPI in a clean environment and validate the Action from a
   separate repository.

Do not create a moving Action tag. Consumers pin the release commit SHA.
