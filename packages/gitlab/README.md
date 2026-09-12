# Quality Graph GitLab

The independently installed GitLab provider and CI runtime for Quality Graph.
Install the CLI and this provider at the same exact release version:

```sh
uv tool install quality-graph-cli==X.Y.Z --with quality-graph-gitlab==X.Y.Z
qg init --provider gitlab
qg generate
qg validate
```

Consumer checks execute as native GitLab jobs. A separate trusted GitLab project
publishes admitted results and handles authorized merge-request commands.

GitLab provenance identifies the instance, project, pipeline and exact job;
GitHub workflow attempts are not used. GitHub is not a runtime dependency.
