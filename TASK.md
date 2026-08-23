# Quality Graph Product Specification

## Status of this document

This document defines the product to build. It is the source of truth for product
scope, public behaviour, architectural constraints, quality standards, and completion
criteria.

It deliberately does not prescribe an implementation plan. The implementing agent must
inspect the source project, validate assumptions, make architectural decisions within
the constraints below, and create its own staged plan.

## Product definition

Quality Graph is a GitHub-native framework for defining repository-specific quality
pipelines as a declarative directed acyclic graph while receiving a complete pull-request
quality experience without building custom CI infrastructure.

The concise product promise is:

> Bring your checks. Quality Graph provides the graph, result protocol, reporting, and
> governance.

Quality Graph is not a collection of Python, Ruby, JavaScript, or other stack-specific
checks. A repository chooses its own commands and tools. The framework supplies the
execution graph integration and the common lifecycle around their results:

- native GitHub Actions jobs and dependencies;
- portable results exchanged between isolated jobs;
- live and final pull-request dashboards;
- GitHub Job Summaries;
- diagnostics attached to source lines;
- configurable pull-request labels synchronized with failing graph state;
- stable findings with reversible administrator approvals;
- authenticated administrator commands;
- correct behaviour across reruns, partial reruns, forks, and concurrent updates;
- validation that generated workflows still match the declared graph.

The framework may ship adapters, presets, and examples for popular result formats and
toolchains. They must remain optional consumers of the public result interface. No
specific check or programming-language ecosystem may become part of the core domain
model.

## Origin and source implementation

The first implementation must be derived from the working Quality Graph and CI
infrastructure in the Monori repository at `~/code/monori-2`.

The Monori implementation is production evidence and the starting codebase, not the
public product interface. Before replacing or extracting behaviour, inspect at least:

- `ci/quality_graph/` for results, dashboards, reports, commands, approval lifecycle,
  registry, execution, and templates;
- `ci/lib/` for GitHub transport, comments, annotations, diagnostics, findings, and
  status handling;
- `ci/tests/` for unit and fake-GitHub integration coverage;
- `.github/actions/` and `.github/workflows/` for permissions, fork handling, artifact
  exchange, reruns, dashboard lifecycle, and workflow contracts;
- the root `pyproject.toml`, `Makefile`, scripts, and development documentation for
  quality practices and tooling.

Reuse proven logic and tests where the abstraction is genuinely general. Refactor or
replace Monori-specific assumptions such as package names, Make targets, fixed check
registries, fixed report markers, repository labels, and hard-coded workflow jobs.

The extraction must preserve the difficult behaviour already solved in Monori,
including authorization isolation, stale approval cleanup, bounded GitHub comments,
newest-attempt artifact selection, read-only fork execution, source annotation limits,
managed comment updates, and protection against unauthorized checkbox edits.

## Target user experience

### Installation and initialization

The intended interaction is a small CLI and committed declarative configuration:

```bash
qg init
qg generate
qg validate
```

The exact distribution mechanism is an implementation decision, but installation must
be suitable for both local development and pinned GitHub Actions usage. The project must
not require a hosted Quality Graph service for its core features.

`qg init` creates an understandable starter configuration. `qg generate` deterministically
compiles that configuration into the required GitHub Actions workflows and graph
manifest. `qg validate` fails when the declaration is invalid or generated files are
stale.

Generated files are committed to the consuming repository so pull requests can inspect
their changes and GitHub can load the workflow before executing it.

### Declarative graph

The human-edited source of truth is `quality-graph.yml`. A representative configuration
should be expressible at approximately this level:

```yaml
version: 1

profiles:
  default:
    runner: ubuntu-latest
    setup:
      - uses: actions/checkout@v7

  python:
    extends: default
    setup:
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked

nodes:
  format:
    title: Formatting
    profile: python
    run: make fmt-check

  lint:
    title: Lint
    profile: python
    needs: [format]
    run: make lint-sarif
    results:
      sarif: reports/lint.sarif

  unit-tests:
    title: Unit tests
    profile: python
    needs: [lint]
    run: make test-unit
    results:
      junit: reports/unit.xml

  integration-tests:
    title: Integration tests
    profile: python
    needs: [unit-tests]
    run: make test-integration
```

This example is illustrative, not a frozen schema. The final schema must stay smaller
and more semantic than the generated GitHub Actions workflow.

The declaration describes user intent:

- stable node identity and human-readable title;
- execution dependencies;
- command or reusable action to execute;
- reusable execution environment profile;
- result adapter and its inputs;
- optional quality policy and administrator controls.

Quality Graph owns the mechanics required to preserve its invariants:

- result artifact naming and transfer;
- job result publication;
- dashboard lifecycle jobs;
- command event handling;
- optional label reconciliation;
- concurrency coordination;
- required publication permissions;
- rerun-attempt reconciliation;
- generated workflow markers and validation.

### Profiles and GitHub escape hatch

Profiles remove repeated runner and setup configuration. They may describe execution
environment concerns such as runner, container, services, setup steps, timeout,
environment variables, and least-privilege permissions.

A constrained GitHub-specific escape hatch is required for execution environments that
cannot be represented portably. It must not allow consumers to replace the framework's
result, dashboard, command, artifact, or authorization lifecycle. If ordinary consumers
must reproduce substantial GitHub Actions YAML inside `quality-graph.yml`, the interface
is too shallow and must be redesigned.

### Native CI execution

Quality Graph compiles the declared graph into native GitHub Actions jobs. It must not
execute the entire graph serially inside one opaque runner job.

Consumers must retain:

- parallel jobs where the graph permits them;
- separate logs and Job Summaries;
- independent runners and containers;
- job-scoped permissions and environments;
- GitHub's normal status and retry experience;
- observable job dependencies.

## Core domain model

### Graph

A graph is a validated, versioned DAG of nodes. At minimum, validation covers:

- cycles;
- duplicate or invalid identifiers;
- unknown dependencies and profiles;
- invalid result adapter configuration;
- impossible or conflicting execution policies;
- unsafe permission combinations;
- incompatibility with the supported schema version.

The first public version should prefer a small set of explicit semantics over a general
expression language. Additional graph semantics must be introduced only after multiple
real consumers demonstrate the need.

### Node

A node is an independently executed and reported graph operation. It is not necessarily
a linter or test. A node may run any trusted repository command, invoke a reusable
action, collect an external result, or aggregate upstream nodes.

A node has a stable identifier. Renaming its display title must not destroy persistent
finding or dashboard identity.

### Result

Every completed node produces a versioned, deterministic, machine-readable Quality
Graph Result. The protocol is a primary public interface and must have a published JSON
Schema.

A result can represent at least:

- node identity and display title;
- lifecycle status such as waiting, in progress, passed, and failed;
- concise Markdown summary;
- ordered human-readable metrics;
- zero or more findings;
- zero or more source annotations;
- reversible administrator controls and explanatory notes;
- provenance needed to reject stale results from another commit or attempt.

A representative result is:

```json
{
  "schemaVersion": 1,
  "nodeId": "lint",
  "title": "Lint",
  "status": "failed",
  "summary": "Found 3 violations",
  "metrics": [
    {"label": "Findings", "value": "3"}
  ],
  "findings": [
    {
      "id": "lint-abc123",
      "severity": "error",
      "message": "Unused import",
      "location": {
        "path": "src/app.py",
        "startLine": 14,
        "endLine": 14
      }
    }
  ]
}
```

The protocol must be usable without importing the implementation language's SDK. Any
tool capable of writing conforming JSON can integrate with Quality Graph.

### Finding

A finding is a stable, individually addressable quality observation. Its identifier must
be deterministic from semantic content chosen by its producer, rather than transient
line position alone. Moving unchanged code should not necessarily create a new finding;
changing the relevant violation should invalidate the previous approval.

A finding may include severity, message, rule identifier, documentation URL, source
range, fingerprint data, and optional grouping metadata. The minimal required fields
must remain small.

### Approval

An approval is a reversible administrator decision that suppresses a currently matching
finding without erasing it from reports. Approvals are distinct from tool-native ignore
directives: they are review decisions recorded by Quality Graph.

Approvals must:

- be restricted to authorized repository administrators or explicitly configured roles;
- target a finding, a file when supported, or a gate/node when explicitly allowed;
- remain visible in summaries and dashboards;
- be automatically discarded when their finding no longer exists;
- survive harmless workflow reruns and source movement when the finding fingerprint is
  stable;
- be removable through the inverse command;
- never be accepted from untrusted edited Markdown or forged hidden markers.

## Result production and adapters

The lowest-friction node runs a command and maps its exit code to pass or fail:

```bash
qg run lint -- make lint
```

For structured output, the first stable release must support:

- the native Quality Graph JSON result;
- SARIF for static-analysis findings;
- JUnit XML for test results.

Additional adapters such as LCOV, Cobertura, Checkstyle, or tool-specific presets may be
added when justified. They must translate external formats into the same result model;
they must not create parallel reporting implementations.

The CLI must offer a non-language-specific way to emit or validate native result JSON.
Optional language SDKs may provide ergonomic builders, but the SDK of the implementation
language must not be required by consumers.

Adapters must handle malformed, missing, oversized, and partially valid reports with
clear deterministic errors. A tool command failure and a result parsing failure are
different conditions and must remain distinguishable in diagnostics.

## GitHub experience

### Job Summary

Every node produces a complete GitHub Job Summary containing its status, metrics,
findings or diagnostics, and links needed to understand the result without reading raw
logs. Large output is bounded and points to retained artifacts or logs.

### Source annotations

Findings with trustworthy repository-relative locations appear as GitHub annotations on
the relevant source lines. Annotation rendering must escape workflow command input,
respect GitHub limits, group duplicate locations sensibly, and tell the user when
additional diagnostics are available only in the summary.

### Pull-request labels

Quality Graph can synchronize pull-request labels with the effective result of the graph
and individual nodes. Label management is optional and can be disabled completely for a
repository.

The declaration must support a clear global switch and optional graph- and node-level
labels. A representative shape is:

```yaml
labels:
  enabled: true
  failing: quality:failed

nodes:
  lint:
    run: make lint
    label:
      failing: quality:lint
```

The final schema may improve this shape, but it must preserve these semantics:

- `labels.enabled: false` prevents all label reads, creation, addition, and removal by
  Quality Graph;
- a configured aggregate failure label is present while the graph has at least one
  effective blocking failure and is removed after the graph recovers;
- a configured node failure label is present while that node has an effective blocking
  failure and is removed after the node passes, becomes non-blocking, or is removed from
  the graph;
- an approved finding no longer contributes to failure when the node's policy considers
  approved findings non-blocking;
- labels owned by Quality Graph are reconciled idempotently, while unrelated repository
  labels are never modified;
- global defaults can be overridden or disabled for an individual node without copying
  the rest of the label configuration;
- label names, colors, descriptions, and whether missing labels may be created are
  configurable when label creation is supported;
- stale or superseded workflow runs cannot remove labels established by a newer result;
- read-only fork jobs do not mutate labels; the trusted publication path performs the
  reconciliation when configured;
- disabling label management leaves existing labels untouched unless the user explicitly
  runs a documented cleanup or reconciliation operation.

Label state is derived from effective Quality Graph state rather than a job's raw process
exit code. This keeps labels consistent with approvals, configured blocking policy,
partial reruns, and aggregate status.

Label synchronization requires `issues: write` in the trusted publication job. The graph
compiler adds that permission only when label management is enabled. A label transport
failure must be visible and actionable without rewriting the underlying check result.

### Conversation dashboard

For eligible pull requests, Quality Graph maintains one managed conversation comment.
It displays:

- aggregate graph status;
- one stable row per visible node in graph order;
- current node status;
- compact metrics;
- links to each Job Summary and logs;
- authorized reversible controls grouped by node;
- notices explaining read-only mode, omitted controls, stale runs, or incomplete data.

The dashboard is initialized early, updated while jobs run, and finalized from portable
result artifacts. It must preserve completed results during partial reruns and choose
the newest valid attempt for each node. Concurrent updates must not corrupt or duplicate
the managed comment.

Dashboard rendering must remain valid Markdown and fit GitHub's comment body limit. The
framework should preferentially retain high-value controls and link to complete Job
Summaries when content must be omitted.

### Administrator commands

The command surface includes, at minimum, status/help and reversible finding approvals:

```text
/qg status
/qg help
/qg ignore lint-abc123
/qg remove-ignore lint-abc123
/qg ignore-file src/legacy.py
/qg ignore lint
```

File-wide and node-wide approvals are opt-in capabilities. Commands may accept multiple
targets where the result remains understandable and reversible.

The framework must parse commands canonically, validate them before dispatch, identify
the target node, authorize the actor, and isolate pending command state. Checkbox-based
controls are a presentation convenience, not an authorization source. Editing the bot's
dashboard text must not execute a command.

Retry and other operational commands are desirable only after their permission and
event semantics are safe and predictable. They are not required merely to make the
first release look feature-rich.

### Pull requests from forks

Untrusted fork code must run without write privileges or repository secrets. Quality
Graph must support a read-only result path and a separate trusted publication path when
GitHub's event and permission model requires it. Fork-originated content must never be
executed in a privileged context.

### Reruns and stale state

Every published result is bound to repository, pull request, head commit, workflow run,
and attempt as appropriate. The aggregator rejects or visibly reports incompatible
artifacts. Re-running a subset of jobs preserves valid results for unchanged nodes while
showing rerun nodes as pending. A superseded run must not overwrite a newer dashboard.

## Compiler and generated workflows

The compiler must produce deterministic, reviewable GitHub Actions YAML. Running
generation twice with identical inputs produces byte-identical outputs.

Generated workflows must be clearly marked as generated, but the generator must not
hide unsafe or surprising behaviour. A reviewer should be able to understand commands,
dependencies, permissions, third-party actions, and event triggers from the generated
file.

`qg validate` compares declarations and generated output without mutating files. It
returns a non-zero exit status with actionable diagnostics when regeneration is needed.

Schema evolution must be explicit. Unsupported versions fail clearly; migrations must
not silently reinterpret existing graphs.

## Architectural constraints

The public seam is the declarative graph plus the versioned result protocol. GitHub
Actions is initially the production adapter at that seam. Core graph validation,
result parsing, approval decisions, and rendering models should be deterministic and
testable independently of live GitHub transport.

The framework must consist of deep modules: callers provide a small amount of semantic
configuration and receive substantial behaviour. Avoid exposing internal orchestration
objects merely to make tests convenient.

The GitHub client is a true external dependency and requires a narrow injected port with
at least production and in-memory/fake adapters. Integration tests exercise the full
runtime against a deterministic fake GitHub HTTP implementation, following the proven
Monori approach.

Generated workflow structure is an observable product output and requires contract and
snapshot-style tests that fail on accidental permission, event, dependency, or artifact
changes.

## Security and trust model

Security is a product feature, not deferred hardening. The implementation must define
and test trust for:

- pull-request source code;
- event payloads and comment bodies;
- actor identity and repository role;
- bot-owned comments and hidden markers;
- generated workflow content;
- artifact provenance;
- filesystem paths embedded in external reports;
- shell commands and arguments;
- GitHub tokens and job permissions.

Use least-privilege job permissions. Checkouts do not persist credentials unless a
specific trusted operation requires them. Commands assembled by the framework avoid
shell interpolation; user-requested shell execution remains visibly declared as such.
Paths from reports are normalized and rejected when they escape the repository.

No secret value may appear in logs, summaries, artifacts, exceptions, or generated
configuration. Tests must cover malicious command text, forged markers, path traversal,
stale artifacts, unauthorized actors, and fork pull requests.

## Public documentation and distribution

The repository is intended for public distribution. All public documentation, GitHub
content, generated user-facing messages, commit messages, issues, and pull requests are
written in clear English.

The eventual documentation set must include:

- a concise README that demonstrates the value before architecture details;
- installation and pinning instructions;
- a minimal working graph;
- configuration reference generated or verified from the schema;
- result protocol and adapter documentation;
- permissions and fork security explanation;
- administrator command reference;
- migration and compatibility policy;
- examples for multiple unrelated stacks;
- contributor and release guidance.

Examples must demonstrate portability. At least Python, JavaScript/TypeScript, and one
non-Python/non-Node ecosystem should be represented before calling the framework
generally available.

## Codebase quality standard

Quality Graph must dogfood the engineering discipline it provides. Until it can compile
and execute its own graph safely, ordinary GitHub workflows may bootstrap the checks.
Once the product supports the required capabilities, the repository must define and run
its own checks through Quality Graph.

The implementation language is expected to be modern Python unless the implementing
agent documents a compelling reason to choose otherwise. Relevant Monori configurations
and practices are the baseline, adapted to this repository rather than copied blindly.

### Dependency and build discipline

- Use `uv`, a committed `uv.lock`, locked CI installs, explicit dependency groups, and a
  build backend suitable for publishing the CLI and library.
- Support the oldest declared Python version in both metadata and CI. Do not accidentally
  rely on a newer interpreter.
- Pin GitHub Actions to reviewed stable major versions or immutable commits according to
  the repository's documented policy.
- Expose routine development operations through clear `Makefile` targets. CI invokes
  those targets so local and CI behaviour cannot drift.
- Generated schemas, workflows, documentation, and distributions have freshness checks.

### Formatting and linting

- Ruff is mandatory for Python linting and formatting.
- The configuration must contain the Ruff equivalent of all rules enabled:

  ```toml
  [tool.ruff.lint]
  select = ["ALL"]
  ```

- The allowlist of ignored rules starts with only formatter conflicts such as `COM812`,
  `D203`, and `D212` when still applicable. Every additional global or per-file ignore
  requires a narrow, documented justification.
- Use a 100-character line length unless project evidence supports changing it.
- Check import ordering, documentation, naming, security rules, complexity, annotations,
  and unused suppressions through Ruff.
- Format and lint Markdown, YAML, JSON, shell, GitHub Actions, and any other tracked source
  formats with appropriate deterministic tools.
- Validate GitHub workflows with `actionlint`; validate shell with `shellcheck` and
  `shfmt`; spell-check public prose.
- Code comments are reserved for non-obvious constraints that code and naming cannot
  express. Public interfaces are documented.

### Static typing

- Run mypy in strict mode with `warn_unreachable`, `extra_checks`, explicit overrides,
  redundant-expression checks, unused-awaitable checks, and unused-ignore checks.
- Disallow explicit, imported, and decorated `Any`; reject implicit optional values and
  untyped definitions.
- Treat external JSON, YAML, XML, and GitHub payloads as untrusted values and narrow them
  at small adapters before they reach the domain model.
- Ship `py.typed` when exposing an importable Python package.

### Tests

- Use pytest and organize tests by observable behaviour.
- Pure graph, protocol, policy, rendering, and compiler behaviour receives fast unit
  tests through public interfaces.
- GitHub lifecycle behaviour receives integration tests against a real local fake HTTP
  implementation rather than broad mocks.
- Generated workflow tests account for every job, dependency, event, permission, action,
  artifact, and command handler required by a declared graph.
- End-to-end fixtures compile representative repositories and exercise the resulting
  workflows or a faithful local execution harness.
- Tests cover success, tool failure, malformed reports, partial results, cancellation,
  reruns, concurrent updates, oversized output, untrusted forks, and authorization
  attacks.
- Flaky tests are defects. Time, polling, identifiers, and transport are injectable or
  controlled where determinism requires it.

### Coverage and mutation testing

- Measure statement and branch coverage across all production Python modules.
- The committed coverage gate is 100% statement and branch coverage for the core domain,
  compiler, protocol, policy, and rendering modules. External transport adapters may use
  a lower repository-wide floor only when unreachable platform behaviour is narrowly
  excluded and the rationale is documented. The initial repository-wide target must not
  be lower than 90%.
- Run diff coverage on changed lines and require 100% for new production code.
- Use `mutmut` for Python mutation testing. The full mutation suite runs on the default
  branch on a scheduled or suitably cached workflow; pull requests run a diff-scoped
  mutation gate.
- The mutation score threshold starts at 90% and may only increase. Surviving mutants are
  fixed with stronger tests or recorded as narrowly justified equivalent mutants; broad
  exclusions are not acceptable.
- Mutation tooling must cover the framework's core decision logic, including graph
  validation, result parsing, approval authorization, aggregation, and generated workflow
  policy.

### Analysis and supply-chain checks

- Run Bandit, Semgrep, and Vulture or rigorously equivalent tools.
- Audit Python dependencies with `pip-audit` against the locked resolution.
- Scan tracked history and changes for secrets with a maintained secrets scanner.
- Check licenses and packaging contents before release.
- Build source and wheel distributions in CI, inspect their contents, install them in a
  clean environment, and smoke-test the installed CLI.
- Run generated GitHub Actions with least privilege and test permissions as contracts.

### Local guardrails

- Provide a safe pre-commit hook installation target based on the Monori pattern: preserve
  unrelated existing hooks, run deterministic formatting, and stage only formatter
  changes created by the hook.
- Provide a complete local quality target covering format check, lint, types, analysis,
  fast tests, and relevant generated-file checks.
- Clean targets validate that every deleted path is inside the repository.

## Repository and change history

The local repository begins on `main`. During initial bootstrapping, changes may be
committed directly to `main` as requested by the project owner. This is a temporary
workflow exception, not a relaxation of review or quality standards.

The history must remain suitable for a public project:

- commits are atomic and independently understandable;
- each commit has one coherent purpose and leaves the repository in a valid state;
- commit messages are one-line, lowercase, imperative English without co-authors;
- generated changes are committed with the source change that caused them;
- mechanical extraction is separated from semantic redesign where practical;
- unrelated cleanup is kept out of feature commits;
- secrets, machine-local paths, caches, reports, and build products are never committed.

Once the bootstrap phase is complete, work proceeds through accumulated GitHub issues,
issue-named branches, and pull requests. All GitHub communication is in English.

## Non-goals for the initial product

- Shipping an opinionated universal set of linters or tests.
- Requiring consumers to use Python, Make, or any specific build system.
- Replacing GitHub Actions with a custom CI scheduler.
- Running every graph node inside one opaque job.
- Providing a hosted control plane, database, or web dashboard.
- Supporting GitLab, Bitbucket, or other CI systems before the GitHub seam is stable.
- Creating a general-purpose policy expression language.
- Hiding arbitrary privileged behaviour behind generated workflows.
- Supporting every report format before the native protocol, SARIF, and JUnit are solid.

## Product completion criteria

The first complete product is reached when all of the following are demonstrably true:

1. A new repository can declare a multi-branch graph in `quality-graph.yml`, generate
   native GitHub Actions workflows, and verify that generated files are current.
2. Independent jobs execute in parallel according to the declared DAG and publish
   versioned portable results.
3. Plain exit codes, native Quality Graph JSON, SARIF, and JUnit results are supported.
4. Each node produces a useful Job Summary and source-located findings produce safe
   annotations.
5. One managed pull-request dashboard shows live and final graph state, metrics, links,
   findings, and bounded administrator controls.
6. Configured failure labels are installed and removed idempotently from effective graph
   and node state, while repositories can disable all label management.
7. Authorized administrators can add and remove stable finding approvals; unauthorized
   actors and edited dashboard Markdown cannot alter approval state.
8. Full reruns, partial reruns, concurrent runs, stale artifacts, cancellations, and fork
   pull requests behave safely and predictably.
9. The generated workflows visibly enforce least privilege and pass workflow contract
   tests.
10. At least three example repositories from unrelated ecosystems use the same public
   graph and result interfaces without framework changes.
11. The project passes its formatting, all-rules Ruff lint, strict typing, static
    analysis, security audits, coverage, integration, packaging, and mutation gates.
12. Quality Graph uses Quality Graph to define and report its own quality pipeline.
13. Public installation, configuration, security, command, adapter, compatibility, and
    contribution documentation is complete enough for a user unfamiliar with Monori.

The implementation is not complete merely because Monori code has been copied into a
new package. It is complete when unrelated repositories can define their own quality
graphs through the small public interface and obtain the full verified GitHub lifecycle
described above.
