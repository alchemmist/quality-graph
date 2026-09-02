# Integration testing

Quality Graph tests GitHub-facing behavior against a stateful fake GitHub service through the
production `HttpGitHubPort`. The testkit has two adapters backed by the same state model and route
implementation:

- an in-process `ThreadingHTTPServer` used by the default integration suite;
- a Docker service used to verify process isolation and base-URL configuration.

Run the fast in-process suite:

```bash
make test-integration
```

Run the same fixture-driven scenarios against Docker:

```bash
make test-integration-docker
```

Set `QG_FAKE_GITHUB_PORT` when port `18080` is unavailable.

## Scenario interface

Tests use three operations:

- `FakeGitHubServer()` starts the in-process adapter;
- `reset(payload)` replaces all repository state and configures failures or request delays;
- `snapshot()` returns observable repository state and request history.

The fixture selects the Docker adapter when `QG_FAKE_GITHUB_URL` is set. Tests must interact with
GitHub through `HttpGitHubPort`; direct state access is reserved for constructing legacy in-process
fixtures and is not available in Docker runs.

The fake models pull requests, commit associations, changed files, comparisons, repository
contents, comments, reactions, labels, permissions, workflow runs and jobs, artifacts, check runs,
reruns, pagination, configured failures, and request delays. Request history supports ordering and
budget assertions.

## Coverage responsibility

Use `MemoryGitHubPort` for pure decision logic and exact single-request contracts. Use the HTTP
testkit whenever behavior depends on multiple requests, state convergence, pagination, transport
errors, timing, ownership, idempotence, or concurrency.

GitHub-facing integration coverage includes:

- publisher live/final recovery, stale-writer rejection, artifacts, check-run idempotence, labels,
  no-op refreshes, and request budgets;
- administrator commands, immutable approval records, reactions, reruns, authorization failures,
  and checkbox rollback;
- managed comments, label ownership, artifact provenance and archive safety;
- pagination, URL encoding, missing resources, typed server failures, delays, and binary downloads.

## Monori audit

Monori introduced its Docker-backed fake GitHub in
[`50089e7`](https://github.com/alchemmist/monori/commit/50089e7096a3a77cb7c20360824ef14f0f77baff).
Its four HTTP suites contained 37 scenarios covering dashboard races, commands, repository-client
behavior, and source gates. Public Quality Graph initially retained only a smaller in-process fake
with two lifecycle scenarios. The migration in
[`a568ad5`](https://github.com/alchemmist/monori/commit/a568ad565f94f17724ca0dcc55bf4b5a3941b2d9)
did not move the complete harness, and Monori later removed the legacy implementation in
[`8689848`](https://github.com/alchemmist/monori/commit/868984892c8bf560424fa740d6d22c341b6b7d93).

The restored testkit keeps the strongest parts of the historical design: a stateful service,
fixture reset and snapshot operations, real HTTP, pagination, fault and delay injection, request
history, and Docker isolation. It deliberately removes Monori-specific markers, workflow names,
fixed gate implementations, and package paths. The route and state model are shared by both
adapters instead of maintaining a separate Docker fake.
