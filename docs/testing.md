# Integration testing

Quality Graph tests GitHub-facing behavior against a stateful fake GitHub service through the
production `HttpGitHubPort`. The testkit has two adapters backed by the same state model and route
implementation:

- an in-process `ThreadingHTTPServer` used by the default integration suite;
- a Docker service used to verify process isolation and base-URL configuration.

Run the fast suite:

```bash
make t-fast
```

Run the medium suite, which executes integration scenarios first in-process and then against the
Docker adapter:

```bash
make t-medium
```

Both adapters run even when the first suite fails; the target fails if either suite or Docker
setup/cleanup fails. Set `QG_FAKE_GITHUB_PORT` when port `18080` is unavailable.

## Scenario interface

Tests use three operations:

- `FakeGitHubServer()` starts the in-process adapter;
- `reset(payload)` replaces all repository state and configures failures or request delays;
- `snapshot()` returns observable repository state and request history.

Use `raw_responses` in the reset payload to map HTTP paths to base64-encoded response bodies for
malformed JSON and encoding-error scenarios. These GET overrides return HTTP 200 with an
`application/json` content type.

The fixture selects the Docker adapter when `QG_FAKE_GITHUB_URL` is set. Tests must interact with
GitHub through `HttpGitHubPort`; direct state access is reserved for constructing legacy in-process
fixtures and is not available in Docker runs. A slow lane is intentionally not defined yet.

The fake models pull requests, commit associations, changed files, comparisons, repository
contents, comments, reactions, labels, permissions, workflow runs and jobs, artifacts, check runs,
reruns, pagination, configured failures, and request delays. Request history supports ordering and
budget assertions.

## Workflow attempt scenarios

Use `workflow_attempt_jobs` in `reset(payload)` to provide job history as
`{"10": {"2": [...], "3": [...]}}` (run ID, attempt number, jobs). This explicit history takes
precedence over legacy `workflow_jobs` and polling snapshots for that run. Include an empty list
for a known attempt with no jobs. Each job fixture should include its GitHub `id`, `run_id`,
`run_attempt`, `name`, `status`, and `conclusion`.

The attempt endpoint `/actions/runs/10/attempts/2/jobs` returns only that attempt and returns 404
for an unknown attempt. `/actions/runs/10/jobs?filter=latest` (also the default) returns the highest
configured attempt; `filter=all` returns the complete history. Filtering precedes pagination.
Artifacts remain scoped to the workflow run, so old artifacts can coexist with newer jobs.

The fake does not execute Actions jobs: a rerun request is recorded, and tests explicitly supply
the resulting attempt history. Artifact fixtures may intentionally contain impossible or
conflicting metadata to exercise the publisher trust boundary.

`test_publication_attempts_http.py` exercises issue #69 through `publish_workflow_run` and the
production HTTP transport. It checks the persisted check conclusion with an exact matching
latest-run response. Stale success after failure/cancellation, future attempts, results on either
side of the event attempt, and conflicting duplicates (both orders and across pages) are ordinary
assertions, without skips or expected-failure markers. Current results, retained results for jobs
not rerun, missing results and wrong provenance provide controls. The regressions cover publisher admission and are also exercised through the watcher and
administrator command paths.

## Coverage responsibility

Use `MemoryGitHubPort` for pure decision logic and exact single-request contracts. Use the HTTP
testkit whenever behavior depends on multiple requests, state convergence, pagination, transport
errors, timing, ownership, idempotence, or concurrency.

GitHub-facing integration coverage includes:

- publisher live/final recovery, stale-writer rejection, artifacts, check-run idempotence, labels,
  no-op refreshes, and request budgets;
- dashboard job Logs URLs through live/final updates, retained result attempts, pagination, and
  explicitly labeled Workflow run fallbacks for missing or ambiguous job metadata;
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

## Check reports

`make t-fast` and `make t-medium` write native producer data to `reports/test-fast.json` and
`reports/test-medium.json`. The repository declaration selects these reports for collection.
The test runner preserves separate `.in-process.xml` and `.docker.xml` files, namespaces findings
by execution group, and emits per-group test/failure/skipped counts. The central renderer displays
test names and assertion traces without requiring pytest to generate GitHub Markdown.

Docker setup and cleanup are recorded even when they fail. Failed setup prevents the Docker test
run, but preserves in-process results and cleanup diagnostics. Missing, malformed or unsafe JUnit
reports produce an actionable failure; a stale XML file is removed before each execution. The
runner exits unsuccessfully when any phase fails, including failures before tests can run.

Other repository command gates use `scripts/check_report.py --output reports/check.json -- command`
to preserve bounded command diagnostics in the same data contract. This supplies useful failure
output, but does not pretend generic logs are source findings. Structured Python gate findings
remain the separate work tracked in issue #40; standard SARIF output can be selected for analyzers
that support it. Release step sequences retain their existing reporting configuration.

The end-to-end reporting tests launch real pytest subprocesses and a deterministic stand-in for
Docker lifecycle commands, then pass the resulting producer file through collection and the Job
Summary renderer. They assert per-execution counts, failing names, traces, setup/cleanup failures,
missing/malformed reports, and the minimal exit-code fallback. They run in both integration lanes;
the outer Docker lane continues to use the real containerized GitHub testkit.
