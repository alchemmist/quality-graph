# GitLab browser acceptance, 2026-09-13

## Environment

Validation used the isolated Colima GitLab laboratory, GitLab CE 19.3.1 and Runner
19.3.0, with real jobs, webhooks, protected publication and candidate wheel packages.
The consumer is `qg-lab/team/consumer`. Playwright 1.62.0 drove Chromium against the
local instance. Playwright MCP was unavailable, so the browser ran through the local
Python package (Chromium 151.0.7922.34). No GitHub pages were accessed through a browser.

API calls prepared disposable repositories and commits and checked job identities.
The browser submitted administrator and Reporter comments, clicked job-log links,
Retry and Cancel, and checked rendered MR tables and native pipeline statuses.

## Defect found and fixed

In MR !57, `/qg ignore-file tests/example.py` was rejected despite the JUnit testcase
having that `file` attribute and the declaration allowing file approvals. The JUnit
adapter discarded the path, so governance had no file target to approve.

The adapter now preserves a repository-relative testcase file as a source location.
It uses line 1; JUnit producers do not share a line-number convention. Absolute
paths and parent traversal fail validation. Finding identity remains unchanged.

A new unit regression failed before the fix and passed afterwards. The permanent
real-GitLab approval scenario now approves and revokes the file and checks that job
IDs do not change. In browser MR !58, file approval and revocation both passed with
the rebuilt package. GitLab correctly displayed `passed with warnings` after approval:
the original failed job remained visible while its effective result passed.

## Fork fixture race found and corrected

One repeat run finished with 8 passing scenarios and a fork-scenario timeout. The
parent pipeline 1778 was created at 10:14:28.314 UTC; the fork MR pipeline 1779
followed 135 ms later and became the current MR pipeline. No successful summary
was published for the obsolete parent execution.

Waiting for diff refs alone was insufficient. The fixture now waits for the fork's
own `merge_request_event` pipeline for the exact SHA before a Maintainer starts the
parent pipeline. The runtime's current-pipeline checks remain intact. The original
failure log is retained with the local browser artifacts.

## Completed browser checks

- Failed quality findings appear in the managed table and block merge.
- `/qg help` and `/qg status` are acknowledged.
- Node, file and finding approvals and their inverse commands update effective state.
- Approval commands do not create duplicate check executions.
- Invalid targets are rejected.
- Reporter comments cannot grant approvals.
- Approvals persist across a new MR head when their targets remain present.
- The summary identifies the current pipeline and links to the current job.
- Command failures remain blocking after an approval request.
- Retry through the job UI creates a new job and updates the summary link.
- Cancel through the job UI produces a canceled MR/pipeline summary.

Browser inspection also covered the real-suite MRs for erased result artifacts,
explicit skips and fork pipelines in the parent project. Each rendered one managed
summary. An erased artifact left native execution successful but effective state
failed; an explicit skip remained visible as skipped.

The final three-node fixture is MR !80:
`http://127.0.0.1:8929/qg-lab/team/consumer/-/merge_requests/80`.
It uses a documentation content check (`docs.txt` must contain `valid`), an
independent verification command and a package command depending on Documentation.

With invalid content, the browser showed Verification passed, Documentation failed
and Package skipped; the native pipeline failed. An approval request did not bypass
the failure. After committing valid content, all three rows and the native pipeline
passed. Job timestamps confirmed that Package started after Documentation finished.
The owned `quality:failed` label was removed while an unrelated label and comment
remained. The native pipeline page displayed every declared check.

All 20 browser checkpoints passed. The final MR remains available for inspection.

## Validation

- 627 fast tests passed.
- 218 HTTP integration tests passed in process and again through Docker.
- The coverage run passed all 845 ordinary tests.
- Formatting, lint, typing, strict documentation build and package installation smoke
  checks passed.
- The final real GitLab suite passed all 9 tests in 385.44 seconds, including the
  file-approval regression and the corrected fork initialization.

Local screenshots and machine-readable checkpoint results are under
`reports/gitlab-browser/`. These ignored artifacts do not contain authentication
state; credentials and browser sessions remain in the private lab state directory.

## Scope and remaining differences

This validates the local GitLab integration and the shared core; it is not a browser
validation of GitHub, GitLab.com, every GitLab version or every browser engine.
Malformed, duplicate and provenance-mismatched evidence are covered by the HTTP
suite, not by browser actions. Packages were installed from candidate wheels;
installation from PyPI must be repeated after release.

The GitLab MR note currently has a result table, findings and log links. It does not
render a dependency diagram or clickable approval controls. These differences are
now explicit in `docs/gitlab.md`; approvals use new `/qg` comments.
