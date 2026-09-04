# Quality Graph Python Gates

Provider-independent source and diff quality gates for Python repositories. The package does not
depend on the GitHub provider and every command can run locally, in Quality Graph, or in another
CI system.

## Installation

```bash
uv add --dev quality-graph-python==0.1.8
```

Diff-based commands compare committed repository content with `origin/main` by default. CI must
fetch the base history. Pass `--base` when the default branch or comparison point differs.

## Commands

| Command | Scope | What fails |
| --- | --- | --- |
| `qg-python-suppressions --base origin/main` | Added lines in Python and common config files | New `noqa`, `nosec`, type ignores, coverage pragmas, or ignore/exclude configuration |
| `qg-python-object-annotations --base origin/main` | Added Python annotation lines | Direct, qualified, or forward `object` annotations |
| `qg-python-triple-quotes --base origin/main` | Added Python string delimiters | One-line triple-quoted strings or multiline delimiters sharing a line with content; real docstrings are excluded |
| `qg-python-time-bombs --base origin/main` | Added Python and shell lines | Integer literals that plausibly encode Unix timestamps in seconds through nanoseconds |
| `qg-python-no-comments packages apps scripts` | Every Python file below the selected roots | Code comments other than shebangs and narrowly recognized tool directives |
| `qg-python-flaky --base origin/main --attempts 3` | Changed Python test files | Consistent failures or mixed pass/fail outcomes across repeated pytest runs |

Diagnostics use `path:line:column: message` and commands return non-zero when findings exist.

## Make integration

Keep graph nodes semantic by routing commands through repository Make targets:

```makefile
BASE ?= origin/main

.PHONY: python-suppressions python-object-annotations python-triple-quotes \
	python-time-bombs python-no-comments flaky-python

python-suppressions:
	uv run qg-python-suppressions --base "$(BASE)"

python-object-annotations:
	uv run qg-python-object-annotations --base "$(BASE)"

python-triple-quotes:
	uv run qg-python-triple-quotes --base "$(BASE)"

python-time-bombs:
	uv run qg-python-time-bombs --base "$(BASE)"

python-no-comments:
	uv run qg-python-no-comments packages apps scripts

flaky-python:
	uv run qg-python-flaky --base "$(BASE)" --attempts 3
```

The no-comments gate intentionally scans complete roots. The other source gates are incremental
and reject newly introduced debt without forcing an immediate cleanup of unchanged legacy code.

## Graph integration

```yaml
profiles:
  python:
    extends: default
    setup:
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --locked

nodes:
  suppressions:
    profile: python
    run: make python-suppressions

  type:
    profile: python
    needs: [suppressions]
    run: make type

  flaky:
    profile: python
    needs: [type]
    run: make flaky-python
```

Use `fetch-depth: "0"` on the checkout setup step when any node compares against a Git base.
