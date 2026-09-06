# Operations and explicit flows

An operation owns an executable contract: command or action, execution profile, environment
variables, timeout, result adapter and local blocking/approval policy. A flow owns when that
contract runs, its node membership, dependencies and presentation. An unused operation is valid.

```yaml
version: 0
provider:
  name: github
  configuration:
    default-branch: main
    runtime:
      action: owner/runtime@1111111111111111111111111111111111111111
profiles:
  default:
    setup:
      - uses: actions/checkout@v7
        with:
          persist-credentials: "false"
operations:
  format:
    run: make fmt-check
    diff-only: true
  lint:
    run: make lint
  test:
    run: make test
flows:
  review:
    trigger: pull-request
    presentation: github-pr
    nodes:
      format: {}
      lint:
        needs: [format]
      test:
        needs: [lint]
  main:
    trigger:
      push:
        branches: [main]
    nodes:
      lint: {}
      test: {}
  release:
    trigger: workflow-dispatch
    concurrency: production
    presentation: release
    nodes:
      verify:
        operation: lint
      test:
        needs: [verify]
        checkpoint:
          environment: prestable
          approval: true
          observation-seconds: 3600
```

Replace the placeholder runtime with the exact commit of a compatible CLI/provider/runtime
release. Explicit flow inputs are unavailable in older pinned Actions. This sample describes a
release contract; it does not deploy anything. See [Release flow contracts](release-flows.md).

## Validation and scheduling

Identifiers use lowercase letters, digits and hyphens, start with a letter, and have at most 63
characters. Operations and flows have independent namespaces. Each placement has a flow-local
node ID and references an operation through `operation`, defaulting to its node ID. A placement
accepts only `operation`, `needs` and an optional release `checkpoint`. It cannot override a
command or execution profile. Aliases use the operation title followed by their local ID in
parentheses so repeated operations remain distinguishable in the PR dashboard.

Every flow validates its complete declared DAG, including when `dependencies: none` removes
scheduling edges. Unknown operations, missing dependencies, self-dependencies, duplicate
dependencies, duplicate IDs and cycles are errors. Dependencies cannot refer to another flow.
Operations cannot declare `needs` or `events`. A `diff-only: true` operation may only appear in a
pull-request flow; membership never silently filters out a misplaced check.

`dependencies` defaults to `graph` for PR and release flows and `none` for push flows. The main
flow runs exactly its explicitly selected checks, concurrently by default. Authors should include
every applicable post-merge check and leave diff-only checks in the PR flow. `dependencies: graph`
can opt a push flow into a DAG. Parallel execution still respects the provider's capacity limits.

The GitHub compiler currently supports at most one PR flow and one push flow, with arbitrary
flow IDs, one executable release flow, and any number of release plans. PR and main flows can be omitted. Push branches
are explicit literal branch names, not wildcard patterns. The PR trigger targets the configured
default branch. Explicit PR workflows do not include the legacy manual-dispatch trigger.

Release flows can use `trigger: {push: {tags: ["v*"]}}` and opt into execution with
`execution: github-actions`. A push trigger selects either branches or tags, never both.
Tag-triggered flows default to graph dependencies and require a concurrency lane. See
[Executable release flows](release-flows.md) for permissions, protected environments and retries.

An operation may define `steps` instead of a single `run` or `uses`; mixing the forms is rejected.
Every step has the same command/action fields as a profile setup step. An operation's optional
`permissions` replace its profile permissions. Its optional `environment` accepts a literal
environment name or `{name: ..., url: ...}`. Write permissions and environments are release-only.

An optional literal `concurrency` lane serializes executions without cancelling an in-progress
run. Without it, PR/main retain the per-workflow/per-ref cancellation policy. Provider concurrency
does not promise FIFO delivery or retain every queued execution. Release execution needs the
stronger durable lane contract described separately.

## Presentation and result identity

`presentation` is an adapter selector: `none` (default), `github-pr` or `release`. These are scalar
selectors in this first interface, not independently configurable feature flags. `github-pr` is
valid only for a PR trigger and enables the existing dashboard, check summary, annotations,
labels and reversible approval controls. `release` is valid only for a release trigger.

PR publication is gated both when generating workflows and when the runtime reads the trusted
base-branch declaration. Main and release have no PR publisher invocation or approval controls.
Main jobs retain raw result artifacts, job summaries, command output and GitHub job conclusions.
They do not emit the PR adapter's annotations. `merge.required: true` requires PR presentation.

Explicit results add paired `provenance.flowId` and `provenance.operationId`; top-level `nodeId`
identifies the placement. The publisher checks all three against trusted flow membership, in
addition to repository, PR, head SHA, run, attempt and declaration digest. An explicit flow cannot
accept an artifact with missing or foreign flow/operation identity. Producer-supplied controls
remain untrusted; the runtime derives controls from policy and strips them for non-PR adapters.

Generated jobs expose `QG_FLOW_ID`, `QG_OPERATION_ID`, `QG_NODE_ID`, `QG_NODE_TITLE` and
`QG_GRAPH_DIGEST` for native producers. Pass the identities to `qg result emit --flow-id ... --operation-id ...`, alongside the existing workflow provenance arguments. Native adapters require
an exact identity/provenance match. Structured adapters use trusted collection context.

## Migrating graph-v0

Legacy `nodes`, node `events` and `execution.<event>.dependencies` remain supported. Their
generated workflow bytes, paths and digest calculation are unchanged by this refactor. Legacy
results can omit flow/operation provenance. There is no removal date in this change.

To migrate:

1. Keep `version: 0`, provider settings and profiles.
1. Move executable node definitions into `operations`, removing their `needs` and `events`.
1. Declare explicit PR/main memberships under `flows`. Copy the PR dependencies into its
   placements; main defaults to parallel. Mark diff-only contracts and keep them PR-only.
1. Set `presentation: github-pr` on the PR flow to retain the interactive product behavior.
1. Update native producers and pin a compatible Action runtime, then run `qg generate` and
   `qg validate`. Commit the declaration, generated files and `.prettierignore` together.

Mixing explicit `operations`/`flows` with legacy `nodes`/`execution` is rejected. Empty operation
catalogues or flow maps are rejected. Explicit declarations use manifest version 1 with separate
`operations` and `flows`, and a new digest. Legacy declarations retain manifest version 0.

PR/main workflow paths remain `quality-graph.yml` and `quality-graph-push.yml`. When a flow or PR
presentation is removed, generation removes its retired compiler-marked workflow; validation
reports a retired file until generation runs. Unmanaged retired files are preserved and cause an
error, requiring the author to resolve the conflict. Generated files should be committed before
migration so removed workflows remain recoverable from version control.

At the Python interface, `Graph.operations` and `Graph.flows` retain the declaration, while
`Graph.for_flow(id)` resolves operation references to nodes for policy evaluation. Compile the
original declaration; compiling a resolved flow is rejected so release projections cannot
accidentally become PR workflows.

## Updating a repository's own declaration

The trusted publisher normally validates artifacts against the base-branch declaration. When
only the declaration digest changes, a publisher containing this migration support can also read
the immutable PR-head declaration and compare its effective PR contract with the base. Acceptance
requires identical node identities, commands, adapters, dependencies, effective profiles, labels,
administrator roles, provider settings, runtime repository and artifact-upload repository. Pin revisions and unrelated flows
may change. Results must then match the actual head declaration digest and flow/operation IDs;
the publisher never rewrites results to claim the base digest.

Changes to PR checks or governance do not qualify and still fail closed. Activate the updated
publisher on the default branch before migrating a repository that runs an older publisher.
Updating only `publisher-action` and its generated workflow is compatible with existing result
digests, so this activation can be reviewed and merged before the configuration migration.
