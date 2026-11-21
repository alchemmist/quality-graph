# Configuration reference

The human-edited source of truth is `qg.yaml`. The provisional machine-readable
schema is [`schemas/graph-v0.schema.json`](https://github.com/alchemmist/quality-graph/blob/main/schemas/graph-v0.schema.json).

## Top-level fields

- `version`: currently `0`.
- `provider.name`: installed provider name; legacy declarations default to `github`.
- `provider.configuration`: opaque provider-owned configuration. The GitHub provider supports an
  explicit `default-branch` contract, an optional `merge.required` synchronization contract, and
  requires `runtime.action` as
  `owner/repository@<40-character-commit>` inside this object. An optional
  `runtime.publisher-action` independently rolls the trusted publisher forward. Set the optional
  `runtime.upload-artifact-action` to an immutable `owner/repository@<40-character-commit>` ref when
  repository policy prohibits mutable action tags.
- `execution`: optional dependency policies keyed by provider-recognized execution event.
- `profiles`: reusable execution environments; `default` is required.
- `nodes`: ordered graph operations keyed by stable node ID.
- `labels`: optional aggregate label management.
- `administration.roles`: `admin`, `maintain`, or `write`; default is `admin`.

Unknown fields fail closed.

`runtime.action` executes inside pull-request jobs and remains part of graph provenance.
`runtime.publisher-action` executes only in the trusted default-branch publisher without checking
out pull-request code. It does not change execution provenance, which permits a reviewed publisher
upgrade to land without invalidating artifacts produced by the existing execution runtime. Both
actions must use the same `owner/repository`; only their immutable commit pins may differ.

The compiler uses `actions/upload-artifact@v7` by default for compatibility. Repositories that
require every action to be immutable can pin the compiler-owned upload step explicitly:

```yaml
provider:
  name: github
  configuration:
    runtime:
      action: alchemmist/quality-graph@0123456789abcdef0123456789abcdef01234567
      upload-artifact-action: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a
```

Changing `runtime.upload-artifact-action` changes the graph digest and regenerates both execution
workflows. The publisher workflow does not execute the upload action.

## Scheduled GitHub publication (opt-in)

To replace `workflow_run`, set `provider.configuration.runtime.publication: scheduled`, and
pin `runtime.publisher-action` to a revision that implements `reconcile`. Use the compiler
from that revision to run `qg generate` and `qg validate`. Leave `runtime.action` and the
required `Quality Graph` check in place. Review and merge the declaration and generated
publisher together: the schedule is **not active until it is on the default branch**.
The setting changes only the publisher, not read-only execution workflows or artifact digests.
Omitting it (or selecting `workflow-run`) retains the existing publisher.

GitHub schedules a tick every five minutes; ticks can be delayed, dropped or disabled by
GitHub. There is no immediate requested/completed notification in this mode. A tick samples
open PRs targeting the repository default branch, posts progress for active executions, and
publishes completed results using the same artifact admission and base governance as the event
publisher. It neither dispatches work from the PR nor accepts caller-supplied run IDs.

The reconciler requires the latest run of `quality-graph.yml` for the exact PR head to have
one unambiguous GitHub PR association, including matching head and base revisions. If the
base moves, update the PR against it and run the checks on the new head. A missing association
(including some fork runs), a different PR sharing that head, or an incomplete run listing
will not produce a new aggregate. GitHub limits a filtered workflow-run search to 1,000 results;
if more runs share the exact head SHA, the reconciler cannot prove the listing is complete.
Use a fresh PR head rather than bypassing the check. Repeated ticks skip
already completed attempts; rerunning creates a new attempt to verify. For protection against
merging a **previously** checked head after the base moves, require branches to be up to date:
GitHub checks attach to a head SHA, not a head/base pair.

## Default branch

`provider.configuration.default-branch` selects the branch used by both generated `pull_request`
and `push` event filters. Values follow Git branch-name constraints and may contain path segments,
for example `main`, `trunk`, or `release/stable`.

`qg init --default-branch <name>` writes the value explicitly without querying GitHub. Existing
declarations that omit it retain the graph-v0 `main` behavior; add the field and regenerate to make
the contract visible and to support a non-`main` repository. Omitted legacy values also preserve
their existing manifest and graph digest, so migration does not invalidate in-flight artifacts.

## Merge requirements

Set `provider.configuration.merge.required` to `true` to make the stable synthetic
`Quality Graph` check a merge requirement. Set it to `false`, or remove the `merge` object, to
remove only that Quality Graph-owned requirement during the next manual synchronization. Node
`policy.blocking` continues to determine which nodes contribute to the aggregate check.

Configuration alone does not mutate GitHub. Run `qg github required-checks sync` explicitly after
changing it. See [required-check synchronization](required-checks.md) for API behavior and token
permissions.

## Profiles

A profile supports one parent through `extends`. Parent setup runs before child setup;
environment, permissions, and services merge with child values taking precedence.

Supported fields are `runner`, `setup`, `env`, read-only `permissions`,
`timeout-minutes`, `container`, and `services`. Setup steps use exactly one of `run` or
`uses`, plus optional `name`, `with`, `env`, `working-directory`, and `shell`.

Execution permissions accept only `none` and `read`. Pull-request jobs cannot request
write access through the declaration.

## Nodes

Node keys are stable IDs matching `[a-z][a-z0-9-]{0,62}`. A node supports:

- `title`, `profile`, ordered `needs` dependencies, and optional `events` selection;
- exactly one `run` command or pinned `uses` action;
- node-level `env` and `timeout-minutes`;
- one result adapter in `results`;
- `policy` for blocking severities and approval scopes;
- optional failure `label` object, string, or `false`.

Validation rejects cycles, self-dependencies, unknown references, duplicate YAML keys,
unsafe paths, mutable runtime refs, and conflicting adapters.

## Event projections

The GitHub provider recognizes `pull-request` and `push`. A node with no `events` field belongs to
both event projections. Set `events: [pull-request]` for checks that inspect only changed code and
must not run after merge.

`execution.<event>.dependencies` controls scheduling independently from event selection. `graph`
preserves declared `needs`; `none` removes scheduling dependencies so the selected nodes start in
parallel:

```yaml
execution:
  pull-request:
    dependencies: graph
  push:
    dependencies: none
```

Omitting `execution` preserves the declared graph for both events. A `graph` projection must be
closed over dependencies: every dependency of a selected node must also select that event. Each
GitHub event projection must contain at least one node.

Quality Graph generates separate pull-request and push workflows. Trusted dashboard publication
continues to observe only the pull-request workflow.

## Policies and labels

`policy.blocking` controls aggregate failure. `blocking-severities` defaults to `error`.
Finding approvals default on; file and node approvals are explicit opt-ins.

Global label management is off by default. Enabling it requires an aggregate `failing`
label. Label objects support `name`, six-digit `color`, `description`, and `create`.
`enabled: false` performs no label API reads or writes.
