# Finding adopters

The landing-page ticker is generated from [`docs/adopters.json`](adopters.json). Each entry names
one GitHub repository and a local logo under `docs/assets`. Add a project only after confirming
that its default branch actively uses Quality Graph and that its logo can be displayed.

To discover public candidates, provide a GitHub token and run:

```bash
GITHUB_TOKEN=… make adopters-find
```

The tool searches GitHub code for strong evidence: the pinned Action or Quality Graph's generated
workflow marker. It deduplicates repositories, ignores forks and archived projects, ranks the
remaining candidates by stars, and marks entries already present in `docs/adopters.json` as
`listed`.

Show only projects that are not yet in the ticker:

```bash
GITHUB_TOKEN=… make users
```

GitHub code search may return repositories that mention Quality Graph without running it. Before
adding a candidate, inspect its `qg.yaml`, generated workflow, and recent Actions runs.
