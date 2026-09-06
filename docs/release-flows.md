# Executable release flows and checkpoint contracts

GitHub release flows can now generate a working `.github/workflows/release.yml` with ordered
operation steps, artifact upload/download actions, protected deployment environments, and
operation-specific permissions. Enable execution explicitly with `execution: github-actions`:

```yaml
flows:
  release:
    trigger:
      push:
        tags: ["v[0-9]+.[0-9]+.[0-9]+"]
    execution: github-actions
    concurrency: publishing
    presentation: release
    nodes:
      build: {}
      publish:
        needs: [build]
```

The compiler supports one executable release flow per declaration. The workflow path is kept
stable for PyPI Trusted Publisher bindings. Every release job requires a tag ref; manual dispatch
is supported when dispatched against a tag. Branch dispatch does not execute release jobs.
Actions used by executable release operations and their setup profiles must be pinned to commit
SHAs. PR and main flows cannot inherit release write permissions or deployment environments.

Operations accept either a single `run`/`uses` command or a nonempty `steps` sequence. Steps run
in declaration order and stop on failure. Put package verification and artifact uploads in the
build operation, downloads and the PyPI action in publishing operations, and GitHub Release
creation in the final operation. Use `permissions: {id-token: write}` and a literal `environment`
for PyPI; grant `contents: write` only to the GitHub Release operation. These permissions replace
the profile's permissions for that job. Each operation still emits a common result and job
summary, without PR dashboard publication or PR approval controls.

GitHub's normal dependency scheduling and failed-job reruns provide failure handling and retries.
Publishing operations must be idempotent or safely reject duplicate publication; generating a
workflow does not make an arbitrary external action transactional. Retained build artifacts must
remain available for a rerun. Concurrency serializes active runs and does not cancel an active
release, but GitHub may replace a pending run and does not provide a durable FIFO queue.

Existing release declarations retain `execution: design-only` by default. Executable flows
containing checkpoints are rejected: durable leases, observation timers, checkpoint resumption,
and rollback remain the future provider contract below. No runner-held observation sleep is
generated. These executable options require a compiler and runtime containing this change;
the published 0.1.9 runtime supports release plans only.

## Checkpointed provider extension

The remaining sections describe requirements for a future stateful executor, beyond the native
tag-bound publishing pipeline implemented above.

## Inputs and lane ownership

Release flows require `dependencies: graph` and a nonempty literal `concurrency` lane. They can
reuse any non-diff-only operation. A manual trigger may carry typed inputs:

```yaml
trigger:
  workflow-dispatch:
    inputs:
      version:
        type: string
        required: true
      target:
        type: choice
        options: [canary, stable]
        default: canary
      dry-run:
        type: boolean
        default: false
```

Supported input types are `string`, `boolean`, `number`, `choice` and `environment`. Defaults must
match the declared type. Choices require unique nonempty options and a default, when present,
within those options. Input IDs are validated. Provider limits and dispatch authorization are
additional execution-time validation, never silently ignored fields. Inputs must be passed as
data, not interpolated into shell source.

Both human dispatch and authenticated API dispatch create a durable release instance. The
instance key includes repository, flow ID and a provider-issued release ID. Freeze the source
commit, complete declaration digest, typed inputs and artifact digests at creation. A lane lease
belongs to that instance across all workflow runs and checkpoints, not to one runner or GitHub
Actions concurrency group. Retrying a dispatch with the same idempotency key returns the same
instance. Another release cannot enter an occupied lane until its owner terminates or an
authorized recovery resolves the lease.

## Provider interfaces and durable state

The future execution adapter must offer these operations with durable storage semantics:

| Interface | Required behavior |
| --- | --- |
| Start | Validate capability, authorize dispatch, freeze inputs and acquire the lane atomically |
| Execute placement | Use `(release, node, attempt)` as idempotency identity and persist result/artifact references |
| Save checkpoint | Compare-and-swap the expected revision, persisting result provenance and continuation state |
| Schedule continuation | Register an external timer or environment event and release the runner |
| Resume | Authenticate the event and atomically consume the matching checkpoint revision once |
| Cancel or fail | Persist a terminal outcome or enter an explicitly configured compensation plan |
| Publish release | Publish idempotent summary/notes/deployment links from durable release state |

Checkpoint storage must survive runner loss, workflow cancellation and provider retries. Its
record includes schema version, release/flow/node/operation IDs, lane, immutable inputs and graph
digest, source commit, monotonic revision, attempts, artifact checksums, target environment,
approval evidence, observation deadline, continuation event identity, terminal outcome and
compensation progress. Credentials must never be persisted in records, artifacts or inputs.

Use compare-and-swap transitions and a transactional outbox for checkpoint/event scheduling.
This prevents a crash between saving a wait and registering its timer from losing the release.
Duplicate, delayed or reordered events are expected. An already-consumed revision is a no-op;
an event for another release, source digest or environment is rejected. A changed declaration
cannot silently resume an existing instance; it requires a separate, audited migration decision.

## Promotion and observation checkpoints

A placement may declare:

```yaml
deploy-prestable:
  operation: deploy
  needs: [package]
  checkpoint:
    environment: prestable
    approval: true
    observation-seconds: 3600
```

This is a provider contract around the operation, not an executable wait operation. Acquire
environment-scoped approval and credentials before invoking the operation. After successful
deployment, persist the exact deployed artifact and an absolute observation deadline. Approval
is environment/provider governance, distinct from reversible PR finding approvals. The provider
must enforce the environment's reviewers and protection rules, issue short-lived credentials
only to the execution attempt, and prevent untrusted PR code from entering this lifecycle.

After deployment, enter `waiting-observation` and release the runner. A protected-environment
wait or timer-backed external continuation resumes only after the persisted deadline and a
successful health observation. The manifest duration is a minimum observation window, not a
shell `sleep` command. Expiry alone is not proof of health. The provider must reject executable
plans requiring health observation when it has no observation adapter.

The lifecycle is `pending → awaiting-approval → running → waiting-observation → ready` for a
checkpointed placement, with cancellation/failure transitions from every nonterminal state.
Ordinary placements use `pending → running → succeeded`. Downstream promotion is scheduled only
when the prerequisite placement is succeeded/ready and its artifact evidence is durable. Resume
does not rerun a completed deployment. If execution completed externally but result persistence
failed, reconcile against the provider's deployment identity before retrying.

## Failure, rollback and presentation

The first contract's default is explicit terminal failure or cancellation: persist the reason,
last verified environment and artifact, mark downstream promotion blocked, publish the terminal
release summary, then release the lane. No implicit rollback is promised. A future rollback
extension must explicitly declare a compensation DAG, previous known-good artifacts,
authorization and idempotency policy. Its states must distinguish rollback requested, running,
succeeded and failed; a failed rollback cannot be rendered as a successful release. An uncertain
deployment outcome holds the lane for authorized reconciliation rather than silently declaring
failure and allowing a conflicting release.

The release presentation adapter consumes release state, common results and deployment evidence.
It can render summary, release notes, artifact links, promotion status and rollback information.
It cannot call the PR dashboard publisher or derive approval from producer-owned report content.
Publishing notes is an explicit authorized release action keyed by release identity. Repeated
continuations must update the same managed release record rather than create duplicate notes.

Before shipping a provider executor, integration tests must cover restart at every checkpoint,
duplicate/out-of-order timer delivery, invalid resume provenance, approval denial/revocation,
cancel during observation, lost leases, concurrent promotions, uncertain deployment outcomes,
idempotent publication and terminal/rollback failures. Tests must verify that no runner is held
through an observation window. Production deployment adapters remain outside this refactor.
