# Permissions and fork security

Quality Graph separates execution from publication.

The `pull_request` workflow executes repository code with read-only permissions, no secrets,
and checkout credentials disabled. Forks and same-repository branches follow the same rule in
this pre-release.

The publisher runs trusted code from the default branch through `workflow_run`. It never
checks out pull-request code. Downloaded artifacts are treated as untrusted data and checked
for metadata digest, archive size, file count, ZIP traversal, symlinks, node identity, pull
request, head SHA, run, attempt, and graph digest before JSON parsing.

For reruns, result admission uses the last execution of each declared job at or before the
publication attempt, resolved through GitHub attempt-specific job history. Retained artifacts
are accepted only for that execution; a rerun with no result cannot reuse an older success.
Future attempts and conflicting results for the same node and attempt fail validation. Identical
duplicate results are accepted. Missing, ambiguous, or incomplete job evidence fails closed.
Raw passed results cannot override failed or cancelled job evidence. Approval policy is applied
only after admission, preserving approvals of valid quality findings. The publisher, watcher, and
administrator commands share this admission path. First attempts accept only attempt-1 artifacts
and need no historical job lookup.

One publisher invocation owns the live dashboard for the complete workflow run. It polls
authoritative GitHub job state and merges every node by its stable graph identity, so parallel
jobs cannot overwrite each other's lifecycle. The watcher finalizes artifact-derived state when
all declared jobs are terminal, while the completed event shares the same concurrency group and
independently repairs the final state. Both paths update the same check run through its stable
workflow-run identity.

Governance configuration always comes from the pull request base SHA. A pull request may
propose graph changes for review, but proposed roles, labels, and controls do not receive
trusted authority before merge.

Generated permissions are job-specific:

- execution: `contents: read` or less;
- publication: `actions: read`, `checks: write`, `issues: write`, `contents: read`, `pull-requests: read`;
- commands: `contents: read`, `pull-requests: read`, `issues: write` for command feedback and the ledger, and `actions: write` for authorized reruns.

Dashboard Markdown is not an authorization source. Checkbox edits are converted to canonical
commands, rolled back, authorized through current collaborator permission, and persisted as
append-only, bot-owned, unedited ledger records.

Do not add secret-bearing PR jobs or replace the trusted publisher with `pull_request_target`.
Credential-bearing execution requires a separate threat-model design.

## Graph evolution

A passing native job proves that a command ran, not that the PR preserved the required
checks. The publisher can admit additional checks when the existing PR contracts and
base governance are unchanged. Renaming, removing, or rewiring an existing required
check is not additive. Neither editing generated YAML nor retrying the same run grants
approval. A `graphDigest` mismatch compares the expected declaration digest with the
one reported by the execution; it is not a checksum failure in the downloaded ZIP.

Until single-PR topology promotion is supported, use two reviewed changes for a DAG
replacement:

1. Prepare a base migration whose **pull-request** projection contains the intended final
   nodes, dependencies, commands and profiles. Keep the current **push** checks in a
   separate push-only configuration if their replacements need code not yet on main.
   Generate and validate the workflows (`qg generate`, then `qg validate`). Review this
   change as a replacement of required checks. If branch protection requires Quality
   Graph, the old contract will block a non-additive migration. Isolate the migration
   from application changes and have the responsible maintainers review the exact
   commit, including generated workflow and permission changes. Only an actor already
   authorized by repository policy to make this exception should merge that reviewed
   revision; record the approval, commit SHA and expected rejection in the migration PR.
   If policy provides no such procedure, stop: this migration cannot proceed under
   the current rules. Do not disable branch protection or relax artifact admission.
1. Update the application PR against that base. Its push graph can now use the new
   implementation and drop the bootstrap checks. Verify that the effective PR contracts
   match (`pr_contract(project_graph(graph, "pull-request"))` for each declaration),
   regenerate and validate, and run the checks on the updated PR head. If governance or
   a required PR contract still differs, update the reviewed base rather than bypassing
   provenance validation.
