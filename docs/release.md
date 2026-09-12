# Releases

The release pipeline is declared in the `release` flow of `qg.yaml` and compiled into
`.github/workflows/release.yml`. Edit the declaration and run `make graph-generate`; do not edit
the generated workflow. The workflow path and the four existing PyPI environments remain unchanged,
so existing Trusted Publisher bindings continue to apply.

The release build operation retains checkout, dependency installation, version verification,
the complete quality gate, and five artifact uploads. Five isolated publishing operations
download those artifacts; GitHub Release creation depends on all five publishers. The flow
serializes active releases without cancelling them. Failed-job reruns use retained artifacts;
duplicate publication is rejected by the external publishers rather than silently ignored.

An exact semantic-version tag triggers the release workflow. The tag version must match all
five workspace distributions.

A release candidate must pass:

- generated schema, workflow, manifest, and example freshness;
- Ruff ALL, strict mypy, static analysis, unit, fake-HTTP integration, and coverage gates;
- dependency and secret audits;
- mutation score threshold;
- deterministic sdist and wheel builds for `quality-graph-core`, `quality-graph-python`,
  `quality-graph-github`, `quality-graph-gitlab`, and `quality-graph-cli`,
  content inspection, clean installation, provider discovery, and provider-free CLI smoke;
- the repository's own generated Quality Graph workflow.

The repository uses tag-restricted GitHub environments and one PyPI pending Trusted Publisher
for each workspace distribution. Every publisher is bound to `alchemmist/quality-graph` and
`.github/workflows/release.yml`. The environments are `pypi` for `quality-graph-core`,
`pypi-python` for `quality-graph-python`, `pypi-github` for `quality-graph-github`,
`pypi-gitlab` for `quality-graph-gitlab`, and `pypi-cli` for `quality-graph-cli`.

Run `make release-setup` once to configure those environments and the initial pending publishers
through the GitHub and PyPI interfaces. The setup uses no API token or repository secret.

Before the first GitLab-enabled release, add `pypi-gitlab` with the same `v*` tag
restriction and register `quality-graph-gitlab` as a pending Trusted Publisher using
that environment. Keep the four already-published projects and their existing
bindings. If the GitLab project already exists, configure its normal publisher
instead of registering a duplicate pending publisher.

GitHub Release creation remains blocked until every package publisher succeeds.
The five publishers contribute ten distribution files. Publication failures are
visible; the release flow does not silently skip an unpublished provider.

The release workflow builds and verifies distributions in an unprivileged job. Five isolated
publication jobs receive only `id-token: write`, and PyPI creates attestations through Trusted
Publishing. A final job creates the GitHub Release from the exact uploaded files. No long-lived
PyPI token is used.

Release procedure:

1. confirm all five Trusted Publishers and their five named environments are configured;
1. update local `main` so it exactly matches `origin/main` with a clean working tree;
1. run `make release-patch`, `make release-minor`, or `make release-major`;
1. wait for all PyPI publication jobs and the GitHub Release job;
1. install the exact versions from PyPI in a clean environment and validate the Action from a
   separate repository. For GitLab, repeat the local MR end-to-end scenario using
   the exact PyPI versions instead of candidate wheels.

The release targets update all five workspace versions and `uv.lock`, run the complete local
quality gate, create one `release vX.Y.Z` commit and annotated tag, then atomically push `main` and
the tag. They stop before commit, tag, or push if the tree is dirty, local `main` differs from
`origin/main`, workspace versions disagree, the tag exists, or any check fails.

Do not create a moving Action tag. Consumers pin the release commit SHA.

## GitLab release validation

`make package` builds the separate GitLab wheel and sdist and performs an isolated
core + CLI + GitLab installation. That smoke test exercises provider discovery,
initialization, generation and validation while verifying that the GitHub provider
is absent. The real GitLab slow suite installs the same candidate wheels in CI jobs.

Run `make t-gitlab-e2e` before accepting a GitLab runtime release. Keep the candidate
validation evidence separate from post-release PyPI installation evidence. Release
scripts retain a single version across all five distributions and update the
GitLab provider's exact core dependency together with the existing providers.
