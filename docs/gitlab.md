# GitLab

The GitLab provider is a separate Python distribution, `quality-graph-gitlab`.
Install it alongside the CLI at the same exact release version. The GitLab runtime
does not depend on the GitHub provider, GitHub Actions or GitHub credentials.

The PyPI commands below require a release that includes the new GitLab distribution.
For development, the local laboratory installs built wheels instead.

## Install and initialize

```sh
uv tool install quality-graph-cli==X.Y.Z --with quality-graph-gitlab==X.Y.Z
qg init --provider gitlab
```

Initialization writes a starter declaration. Before generation, set the instance URL and
the publisher account's numeric user ID in `qg.yaml`; MR publication cannot be generated
without that identity:

```yaml
version: 0
provider:
  name: gitlab
  configuration:
    default-branch: main
    server-url: https://gitlab.example.com
    runtime-version: X.Y.Z
    publisher-user-id: 42
    runner-tags: [qg-execution]
    publisher-tags: [qg-publisher]
profiles:
  default:
    container: python:3.12-slim
administration:
  roles: [maintain, admin]
nodes:
  test:
    title: Tests
    run: python -m unittest discover -s tests -v
```

Then run:

```sh
qg generate
qg generated-files
qg validate
```

Commit the declaration and generated files. The provider generates `.gitlab-ci.yml`,
`.qg/gitlab.json` and `.qg/gitlab-publisher.yml`. Existing commands remain in the
declaration; the generated jobs invoke the installed runtime once per check.

The image must provide Python 3.12 or later and pip. Choose an image containing
your check tools, or install them through profile setup commands. The default slim
Python image does not include every language toolchain or `make`.

For an existing hand-maintained `.gitlab-ci.yml`, configure
`ci-path: .qg/gitlab-ci.yml` and include it from that file:

```yaml
include:
  - local: .qg/gitlab-ci.yml
```

Generation refuses to overwrite unmanaged CI files. A standalone generated CI has
this shape; the complete generated file also includes publication admission jobs:

```yaml
default:
  image: python:3.12-slim
  tags: [qg-execution]
  before_script:
    - python -m pip install quality-graph-gitlab==X.Y.Z
qg:mr:test:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  script:
    - qg-gitlab execute --flow mr --node test
  artifacts:
    when: always
    paths: [.qg/results/mr/test.json]
```

## Configure trusted publication

Use a separate service project on the same GitLab instance. Its protected default
branch contains the generated `.qg/gitlab-publisher.yml` as `.gitlab-ci.yml`.
The publisher installs the pinned runtime with `GIT_STRATEGY: none`; it never runs
consumer checkout, setup commands, caches or repository scripts.

1. Protect the consumer target branch and publisher default branch. Configure the
   consumer's merge checks to require successful pipelines and disallow success
   from skipped pipelines.
1. Create a dedicated publisher account and an `api` access token. The supported
   setup grants this account Maintainer access to the consumer and publisher
   projects. Store the token only in the service project's masked, protected
   `QG_GITLAB_TOKEN` variable.
1. Set the service project's protected `QG_GITLAB_PROJECTS` variable to a JSON array
   of allowed consumer project IDs, for example `[123]`. The runtime does not trust
   webhook payloads to expand this list.
   Disable pipeline-variable overrides in the service project by setting the
   minimum override role to **No one allowed**. This prevents a trigger-token holder
   from changing package indexes, Python paths, tokens or the allowed-project list.
   Keep configuration in protected project variables, not trigger/schedule variables.
1. Register a consumer execution runner with `qg-execution` and a separate protected
   publisher runner with `qg-publisher`. Keep execution caches and credentials
   separate. Do not expose a host container socket to check jobs.
1. Create a pipeline trigger token for the service project. In the consumer project,
   configure a webhook with job, merge-request and comment events, using
   `https://gitlab.example.com/api/v4/projects/PUBLISHER_ID/ref/main/trigger/pipeline?token=TRIGGER_TOKEN`.
   Keep the trigger token in webhook settings, not in consumer job variables.
1. Add a scheduled publisher pipeline on its protected branch for missed-event
   recovery. A manual web pipeline on the same branch also reconciles current state.

Pipeline events are intentionally not used to trigger publisher pipelines: GitLab
documents a loop-prevention restriction for that path. Jobs and notes can generate
several events, so publisher writes are serialized and unchanged state produces no
new summary or status. [GitLab trigger documentation](https://docs.gitlab.com/ci/triggers/).

Publisher processing does not re-execute the checks. Each new check job, including
a retry, first waits for its own publisher acknowledgement. The acknowledgement is
written only after the trusted external status is installed. This prevents an old
successful status from authorizing a newly retried job before its results are read.

GitLab's built-in `CI_JOB_TOKEN` remains available to execution jobs with GitLab's
normal restrictions. The Quality Graph write token is never available there. Do
not store other unprotected write credentials in the consumer project.

## Results, retries and commands

Native GitLab job state and admitted result evidence are both required. A result
binds server, execution project, target project/MR, SHA, pipeline, exact job ID,
graph digest, flow and operation. GitHub run attempts are not used.

Missing, malformed, mismatched or conflicting evidence cannot establish success.
A current job cannot reuse an older retried job's artifact. Findings are approvable
only after admission; approvals do not suppress command, protocol or infrastructure
failures. Native cancellation is published as `canceled`.

Use new MR comments for commands:

```text
/qg status
/qg help
/qg ignore <finding-or-node>[,<target>...]
/qg remove-ignore <finding-or-node>[,<target>...]
/qg ignore-file <path>[,<path>...]
/qg remove-ignore-file <path>[,<path>...]
```

`maintain`, `admin` and `write` map to GitLab Maintainer, Owner and Developer access,
respectively. The starter allows Maintainer/Owner. Commands are authorized using
current target-project membership and the protected target declaration. Bot-owned
immutable records retain accepted decisions; repeated delivery is idempotent.
Editing a checkbox, hidden marker or another user's comment is not authorization.

The managed summary shows execution and effective results separately. Unrelated
notes and labels are preserved. Labels are managed only when explicitly declared
by the trusted graph.

## Pipelines and supported differences

- MR checks target the configured protected default branch. Ordinary push checks
  run on branches; open MRs suppress duplicate branch pipelines. Explicit push flows
  can restrict their branch membership.
- Explicit MR flows use `presentation: gitlab-mr`; `none` opts out of publication.
- A legitimate explicit result skip remains a skip. Missing evidence is not a skip,
  and a skipped blocking job without a result cannot silently turn the graph green.
- Approvals update the effective status without re-running all checks.
- The MR note currently provides a status table, findings and job-log links. It does not
  render a dependency diagram or clickable approval controls; use new `/qg` comments.
- GitHub `uses`, GitHub runner labels, deployment permission escape hatches,
  custom flow concurrency lanes, executable release flows, merged-results pipelines and merge trains are not
  translated implicitly. Unsupported settings fail validation or execution.
- The execution image must support the Python runtime. Arbitrary GitLab YAML
  fragments and service escape hatches are not part of this first interface.

For fork contributions, a Maintainer starts the MR pipeline in the **target project**
after reviewing the fork's CI changes. That pipeline executes fork code without the
publisher token and is evaluated against target-project governance. A fork-only
pipeline cannot assume the publisher has write access to the fork. Do not grant a
target-project write token to fork jobs to work around this restriction.

## Self-managed GitLab

Configure `server-url` with the canonical instance URL. `api-url` can override the
API endpoint, including a relative installation prefix or a separate API hostname.
The publisher also accepts protected `QG_GITLAB_SERVER_URL` and
`QG_GITLAB_API_URL` overrides; otherwise it uses its GitLab CI environment.

Project IDs are used for authority checks. HTTP helpers also support URL-encoded
paths such as `group/team/project`. HTTPS is required for API endpoints outside loopback. An isolated private HTTP laboratory
can explicitly set `QG_GITLAB_INSECURE_HTTP_HOST` to its exact API hostname in both
execution and protected publisher environments. This exception sends credentials without
TLS and must not be used for public instances. TLS verification remains enabled. Use the
standard CA configuration for private certificate authorities.

Artifact redirects are followed without forwarding GitLab credentials or cookies.
HTTP redirects from an HTTPS API, unsafe archive entries and oversized responses
are rejected. Configure self-managed webhook networking according to the instance's
policy; the local lab explicitly permits callbacks inside its private network.

The real laboratory pins GitLab CE 19.3.1 and Runner 19.3.0. The implementation uses
Free-tier APIs; cloud execution is not part of the local validation evidence.

## Migrate from GitHub

Keep repository commands, graph dependencies and report adapters. Install the
GitLab provider alongside the CLI, change the provider declaration, replace GitHub
setup actions with shell commands, select the execution image and runner tags, and
use `gitlab-mr` presentation for explicit MR flows.

Configure and validate the publisher before enabling merge enforcement. Generate
and commit the GitLab outputs, verify a real MR, then retire the old GitHub workflows
as an explicit repository migration. The GitLab provider does not delete unrelated
GitHub CI automatically. Existing GitHub Result v0 remains supported; GitLab emits
Result v1 and accepts native results with matching GitLab provenance.
