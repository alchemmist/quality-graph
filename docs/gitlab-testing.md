# GitLab test laboratory

There are two independent integration layers.

`make t-medium` runs stateful fake GitHub and GitLab HTTP scenarios first in process
and then through container endpoints. The same state and route implementations back
both adapters. Set `QG_FAKE_GITLAB_PORT` to override the GitLab fake's default port
18081\. Tests interact through the production transport, `reset()` and `snapshot()`.

The real laboratory executes actual GitLab jobs, webhooks and package installations:

```sh
make gitlab-up
make gitlab-seed
make t-gitlab-e2e
make gitlab-status
make gitlab-logs
make gitlab-down
```

The slow suite is explicitly selected from `tests/gitlab/real_scenarios.py`; ordinary
unit/HTTP tests do not silently depend on a running GitLab server. A missing or
broken lab fails the explicit slow suite rather than skipping it.

## Container engine

The macOS development setup uses an isolated Colima Docker profile:

```sh
colima start qg-gitlab --cpu 6 --memory 12 --disk 60 \
  --runtime docker --vm-type vz --activate=false --ssh-config=false \
  --mount "$(pwd):w"
```

The profile does not replace the existing Podman machine or default Docker context.
Set `QG_GITLAB_DOCKER_CONTEXT` to use another Docker context. The lab requires the
`docker-compose` executable. For HTTP fake tests on this profile, use:

```sh
make t-medium COMPOSE='docker-compose --context colima-qg-gitlab'
```

The Compose definition pins official multi-architecture GitLab CE 19.3.1 and Runner
19.3.0 image digests. HTTP and SSH ports are bound to host loopback. The host API is
available at `http://127.0.0.1:8929`; containers use `http://gitlab:8929` through the
private lab network.

## Bootstrap and package installation

`gitlab-seed` creates nested consumer/publisher projects, a publisher account, a
Reporter account, separate execution/protected runners, protected publication
variables and job/MR/comment webhooks. Only bootstrap uses the administrator token.
Publisher jobs use the dedicated scoped account.

`gitlab-wheels` builds and checks distributions, prepares Linux dependency wheels
and serves the candidate wheelhouse inside the private network. Real jobs use
`PIP_NO_INDEX=true`; they cannot accidentally load an older same-version core from
PyPI or import source files from the development checkout.

Credentials and mutable state are stored under the ignored `tests/gitlab/.state/`
directory with private permissions. They are excluded from Docker build contexts.
The ordinary `down` target preserves persistent GitLab data for debugging.

The laboratory contains disposable data only. Tests update their own consumer
declaration and close previous lab MRs before starting a new scenario. They do not
run against an external GitLab account or production project.

After a package release, repeat the consumer scenario using the exact PyPI versions
instead of the candidate wheelhouse. A successful candidate test does not claim
that the distribution has already been published.
