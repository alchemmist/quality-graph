# Monori Python quality parity

| Monori gate | Quality Graph status | Rationale |
| --- | --- | --- |
| Workflow graph | Transferred | `qg validate` checks graph and generated output freshness. |
| Format | Transferred | Ruff, mdformat, shfmt, and generated schemas/workflows. |
| Lint | Transferred | Ruff ALL, YAML, Markdown, shell, spelling, and Actions linting. |
| Type | Transferred | Strict mypy across every workspace package. |
| Static analysis | Transferred | Bandit, Semgrep, and Vulture. |
| Suppressions | Transferred | `qg-python-suppressions` rejects new source/config suppressions. |
| Object annotations | Transferred | `qg-python-object-annotations` rejects changed `object` annotations. |
| Triple quotes | Transferred | `qg-python-triple-quotes` checks changed Python strings. |
| Time bombs | Transferred | `qg-python-time-bombs` checks changed source literals. |
| No comments | Transferred | `qg-python-no-comments` enforces the repository comment policy. |
| Fast tests | Transferred | Unit tests run independently. |
| Medium tests | Transferred | Integration tests run independently. |
| Slow product/E2E tests | Irrelevant | Quality Graph has no application stack or browser product. |
| Coverage | Transferred | Absolute branch coverage and 100% Python diff coverage. |
| Flaky tests | Transferred | Changed Python test files are repeated without pytest retries. |
| Mutation | Transferred | Diff-triggered and scheduled full Mutmut gates. |
| Audit | Transferred | pip-audit and Gitleaks. |
| Frontend mutation/build/bundle size | Irrelevant | No frontend product is shipped. |
| Browser/backend performance | Irrelevant | No serving application or performance SLA exists. |
| Docker application lint/E2E | Irrelevant | The repository ships Python packages and a composite Action. |

The expanded graph is validated end to end by pull requests based on the current
default branch: all 16 declared nodes must publish successful results before the
aggregate `Quality Graph` check succeeds.
