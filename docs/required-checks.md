# Required-check synchronization

Quality Graph can synchronize the stable aggregate `Quality Graph` check with GitHub merge
requirements. Node and job names are deliberately not required contexts: `policy.blocking`
determines which nodes contribute to the aggregate result, so renaming a node does not strand a
pull request behind a stale required check.

Enable the contract in `qg.yaml`:

```yaml
provider:
  name: github
  configuration:
    merge:
      required: true
```

Then run the explicit administrative command:

```console
GITHUB_REPOSITORY=owner/repository GITHUB_TOKEN=token qg github required-checks sync
```

The command reads the configured `default-branch`, prints the planned context additions and
removals, and only then applies them. It never runs during `qg generate`, validation, or workflow
execution. Repeating it with unchanged configuration performs no mutation.

For classic branch protection, synchronization changes only the required-status-checks endpoint,
preserving strictness and unrelated contexts. For a repository ruleset that applies to the default
branch, it preserves the ruleset conditions, enforcement, bypass actors, unrelated rules, and
unrelated checks. Removing the opt-in and synchronizing removes only the `Quality Graph` context.

The token needs repository `Administration: write` permission. A `403` response is reported as a
permission error without exposing response data. Organization-owned rulesets require organization
`Administration: write` and cannot be changed by the repository-scoped command; update the
organization ruleset with an organization administrator instead. If multiple repository rulesets
apply, synchronization fails rather than choosing one ambiguously.
