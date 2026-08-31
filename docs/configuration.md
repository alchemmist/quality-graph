# Configuration reference

The human-edited source of truth is `quality-graph.yml`. The provisional machine-readable
schema is [`schemas/graph-v0.schema.json`](https://github.com/alchemmist/quality-graph/blob/main/schemas/graph-v0.schema.json).

## Top-level fields

- `version`: currently `0`.
- `provider.name`: installed provider name; legacy declarations default to `github`.
- `provider.configuration`: opaque provider-owned configuration. The GitHub provider supports an
  explicit `default-branch` contract and requires `runtime.action` as
  `owner/repository@<40-character-commit>` inside this object. An optional
  `runtime.publisher-action` independently rolls the trusted publisher forward.
- `profiles`: reusable execution environments; `default` is required.
- `nodes`: ordered graph operations keyed by stable node ID.
- `labels`: optional aggregate label management.
- `administration.roles`: `admin`, `maintain`, or `write`; default is `admin`.

Unknown fields fail closed.

`runtime.action` executes inside pull-request jobs and remains part of graph provenance.
`runtime.publisher-action` executes only in the trusted `workflow_run` publisher without checking
out pull-request code. It does not change execution provenance, which permits a reviewed publisher
upgrade to land without invalidating artifacts produced by the existing execution runtime. Both
actions must use the same `owner/repository`; only their immutable commit pins may differ.

## Default branch

`provider.configuration.default-branch` selects the branch used by both generated `pull_request`
and `push` event filters. Values follow Git branch-name constraints and may contain path segments,
for example `main`, `trunk`, or `release/stable`.

`qg init --default-branch <name>` writes the value explicitly without querying GitHub. Existing
declarations that omit it retain the graph-v0 `main` behavior; add the field and regenerate to make
the contract visible and to support a non-`main` repository.

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

- `title`, `profile`, and ordered `needs` dependencies;
- exactly one `run` command or pinned `uses` action;
- node-level `env` and `timeout-minutes`;
- one result adapter in `results`;
- `policy` for blocking severities and approval scopes;
- optional failure `label` object, string, or `false`.

Validation rejects cycles, self-dependencies, unknown references, duplicate YAML keys,
unsafe paths, mutable runtime refs, and conflicting adapters.

## Policies and labels

`policy.blocking` controls aggregate failure. `blocking-severities` defaults to `error`.
Finding approvals default on; file and node approvals are explicit opt-ins.

Global label management is off by default. Enabling it requires an aggregate `failing`
label. Label objects support `name`, six-digit `color`, `description`, and `create`.
`enabled: false` performs no label API reads or writes.
