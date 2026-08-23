# Permissions and fork security

Quality Graph separates execution from publication.

The `pull_request` workflow executes repository code with read-only permissions, no secrets,
and checkout credentials disabled. Forks and same-repository branches follow the same rule in
this pre-release.

The publisher runs trusted code from the default branch through `workflow_run`. It never
checks out pull-request code. Downloaded artifacts are treated as untrusted data and checked
for metadata digest, archive size, file count, ZIP traversal, symlinks, node identity, pull
request, head SHA, run, attempt, and graph digest before JSON parsing.

Governance configuration always comes from the pull request base SHA. A pull request may
propose graph changes for review, but proposed roles, labels, and controls do not receive
trusted authority before merge.

Generated permissions are job-specific:

- execution: `contents: read` or less;
- publication: `actions: read`, `checks: write`, `issues: write`, `pull-requests: read`;
- commands: the same read access plus `actions: write` for rerunning failed jobs.

Dashboard Markdown is not an authorization source. Checkbox edits are converted to canonical
commands, rolled back, authorized through current collaborator permission, and persisted as
append-only, bot-owned, unedited ledger records.

Do not add secret-bearing PR jobs or replace the trusted publisher with `pull_request_target`.
Credential-bearing execution requires a separate threat-model design.
