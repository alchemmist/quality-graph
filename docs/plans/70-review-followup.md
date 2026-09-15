# PR 76 review follow-up

## Review fixes

- MR publication requires a positive `publisher-user-id` at generation time. Provider
  initialization writes a starter for editing; generation remains strict. Branch-only
  and MR flows with `presentation: none` do not require a publisher identity.
- The local wheelhouse recognizes Linux `aarch64` as ARM64 as well as macOS `arm64`.
- A native successful job cannot admit a failed portable result; the regression now
  constructs a valid failed result with its required failure kind.
- Credential-bearing API requests require HTTPS outside loopback. The isolated lab
  explicitly permits its `gitlab` hostname through `QG_GITLAB_INSECURE_HTTP_HOST` in
  both execution and protected publisher variables. API endpoint overrides are checked
  independently, so the exception does not authorize another host.
- JUnit reports retain full counts while serializing at most 10,000 findings and 100
  traces. Omission counts are explicit. Boundary regressions cover 10,000 and 10,001
  distinct failures.

## Red aggregate with green jobs

Run 34826528004 on commit 470b752 had 18 successful native jobs. Its aggregate failed
because the trusted publisher rejected result provenance for artifact 10341945191.
The PR introduces the Documentation node; the old declaration-migration check demands
an identical PR contract. Its fallback table shows native job conclusions even when
artifact admission fails, explaining the green rows and red aggregate.

The publisher now admits additional nodes only if all original node contracts,
dependencies, provider settings, profiles used by original nodes and governance remain
identical. It validates artifact provenance against the candidate declaration and
requires results for added nodes. Failed or missing added-node results cannot produce
success. Existing command, dependency and policy changes remain rejected. Retry evidence
is recalculated for the expanded node set. Fallback copy explicitly distinguishes native
job conclusions from verified artifacts.

The real repository declarations satisfy this rule: Documentation is the only added
PR node. HTTP regressions verify added-node success, failure and missing artifacts.

The publisher-only change is isolated in commit `f639aa7` for a trusted-base rollout.

This does not hot-update the current PR's publisher. Its workflow runs from the trusted
base branch and uses a pinned runtime. Deployment of this publisher change through the
trusted base configuration is needed before rerunning the aggregate. Do not rewrite or
ignore provenance, manually set a successful check, or execute untrusted PR code with
publisher credentials to bypass that boundary.

## Validation

- 659 fast tests passed.
- 238 HTTP integration tests passed in process and through Docker.
- All 18 declaration-migration cases passed again in both modes after final edits.
- Changed-line coverage was 100%.
- Formatting, lint, typing, package installation smoke and strict site build passed.
- All 9 real GitLab scenarios passed in 213.16 seconds using rebuilt wheels and the
  explicitly scoped private HTTP exception.
