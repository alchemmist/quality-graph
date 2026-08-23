# Contributing

Read [TASK.md](TASK.md) before changing product behavior. Public interfaces are the declarative
graph and versioned result protocol; GitHub mechanics remain behind those interfaces.

```bash
make install
make tools
make check
```

Install the optional safe hook with `make precommit-install`. It refuses to replace an unknown
hook, formats staged Python and Markdown blobs through a temporary index snapshot, preserves
unstaged hunks, then runs strict typing and unit tests.

Production code uses no comments unless a constraint cannot be expressed through names, types,
or module structure. Public interfaces and user-visible behavior require English documentation.

Changes after bootstrap use a GitHub issue, a branch named exactly as the issue number, and a
pull request. Commits are atomic, imperative, lowercase, and contain no co-author trailers.
Generated output belongs in the same commit as its source.

New production logic requires complete statement and branch coverage. Core decision logic also
requires mutation tests. Integration behavior uses the real HTTP adapter against the local fake
GitHub server rather than broad mocks.
