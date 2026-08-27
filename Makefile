PYTHON_SOURCES := packages apps tests scripts
PYTHON ?= python3
BASE ?= origin/main
TOOLS_BIN := $(CURDIR)/.tools/bin
MARKDOWN_SOURCES := README.md $(wildcard docs/*.md packages/*/README.md apps/*/README.md)

.DEFAULT_GOAL := check

.PHONY: install tools schemas schemas-check graph-generate graph-validate \
	fmt fmt-check lint type analyze test test-unit test-integration coverage \
	python-suppressions python-object-annotations python-triple-quotes \
	python-time-bombs python-no-comments coverage-diff flaky-python \
	mutation mutation-diff audit package check clean fmt-staged \
	precommit-install precommit-uninstall examples-generate examples-check \
	release-setup

install:
	uv sync --locked --all-groups --all-packages

release-setup:
	bash scripts/setup-release-publishing.sh

tools:
	QUALITY_GRAPH_TOOLS_BIN="$(TOOLS_BIN)" bash scripts/install-tools.sh

fmt-staged:
	uv run --locked --all-packages python scripts/format_staged.py

precommit-install:
	@hook=$$(git rev-parse --git-path hooks)/pre-commit; \
	if [ -e "$$hook" ] && ! grep -q '^# quality-graph-pre-commit-hook$$' "$$hook"; then \
		echo "Refusing to replace an existing non-Quality-Graph hook: $$hook"; \
		exit 1; \
	fi; \
	install -m 755 scripts/pre-commit "$$hook"; \
	echo "Installed Quality Graph pre-commit hook at $$hook"

precommit-uninstall:
	@hook=$$(git rev-parse --git-path hooks)/pre-commit; \
	if [ -e "$$hook" ] && grep -q '^# quality-graph-pre-commit-hook$$' "$$hook"; then \
		$(PYTHON) -c 'from pathlib import Path; import sys; Path(sys.argv[1]).unlink()' "$$hook"; \
		echo "Removed Quality Graph pre-commit hook from $$hook"; \
	else \
		echo "Quality Graph pre-commit hook is not installed"; \
	fi

schemas:
	uv run --locked --all-packages qg schema --output schemas/graph-v0.schema.json
	uv run --locked --all-packages qg result schema --output schemas/result-v0.schema.json

schemas-check:
	@graph_schema=$$(mktemp); result_schema=$$(mktemp); \
	uv run --locked --all-packages qg schema --output "$$graph_schema"; \
	uv run --locked --all-packages qg result schema --output "$$result_schema"; \
	cmp schemas/graph-v0.schema.json "$$graph_schema"; graph_status=$$?; \
	cmp schemas/result-v0.schema.json "$$result_schema"; result_status=$$?; \
	rm -f "$$graph_schema" "$$result_schema"; \
	exit $$((graph_status || result_status))

graph-generate:
	uv run --locked --all-packages qg generate

graph-validate:
	uv run --locked --all-packages qg validate

examples-generate:
	@for example in examples/python examples/typescript examples/go; do \
		uv run --locked --all-packages qg generate --root "$$example"; \
	done

examples-check:
	@for example in examples/python examples/typescript examples/go; do \
		uv run --locked --all-packages qg validate --root "$$example"; \
	done

fmt: schemas graph-generate examples-generate tools
	uv run --locked --all-packages --group format ruff check $(PYTHON_SOURCES) --fix
	uv run --locked --all-packages --group format ruff format $(PYTHON_SOURCES)
	uv run --locked --all-packages --group format mdformat $(MARKDOWN_SOURCES)
	@files=$$(git ls-files '*.sh'); [ -z "$$files" ] || "$(TOOLS_BIN)/shfmt" -w $$files

fmt-check: schemas-check graph-validate examples-check tools
	uv run --locked --all-packages --group format ruff check $(PYTHON_SOURCES)
	uv run --locked --all-packages --group format ruff format --check $(PYTHON_SOURCES)
	uv run --locked --all-packages --group format mdformat --check $(MARKDOWN_SOURCES)
	@files=$$(git ls-files '*.sh'); [ -z "$$files" ] || "$(TOOLS_BIN)/shfmt" -d $$files

lint: tools
	uv run --locked --all-packages --group lint ruff check $(PYTHON_SOURCES)
	@files=$$(git ls-files '*.yaml' '*.yml'); \
	uv run --locked --all-packages --group lint yamllint .yamllint.yaml $$files
	uv run --locked --all-packages --group lint codespell --skip='*/node_modules/*,*/.venv/*,*/reports/*' \
		$(MARKDOWN_SOURCES) packages apps tests examples
	@files=$$(git ls-files '*.sh'); [ -z "$$files" ] || uv run --locked --all-packages --group lint shellcheck $$files
	@files=$$(git ls-files '*.sh'); [ -z "$$files" ] || "$(TOOLS_BIN)/shfmt" -d $$files
	@files=$$(git ls-files '.github/workflows/*.yaml' '.github/workflows/*.yml'); \
	[ -z "$$files" ] || "$(TOOLS_BIN)/actionlint" -shellcheck= $$files

type:
	uv run --locked --all-packages --group type mypy

analyze:
	uv run --locked --all-packages --group analyze bandit -q -c pyproject.toml -r packages apps
	uv run --locked --all-packages --group analyze vulture
	uv run --locked --all-packages --group analyze semgrep --error --quiet --config p/python packages apps

python-suppressions:
	uv run --locked --all-packages qg-python-suppressions --base "$(BASE)"

python-object-annotations:
	uv run --locked --all-packages qg-python-object-annotations --base "$(BASE)"

python-triple-quotes:
	uv run --locked --all-packages qg-python-triple-quotes --base "$(BASE)"

python-time-bombs:
	uv run --locked --all-packages qg-python-time-bombs --base "$(BASE)"

python-no-comments:
	uv run --locked --all-packages qg-python-no-comments packages apps scripts

test: test-unit test-integration

test-unit:
	uv run --locked --all-packages --group test pytest -q -m "not integration"

test-integration:
	uv run --locked --all-packages --group test pytest -q -m integration

coverage:
	uv run --locked --all-packages --group test pytest -q --cov=quality_graph_core \
		--cov=qg_github --cov=qg_cli --cov=qg_python --cov-branch \
		--cov-report=term-missing --cov-report=xml:coverage.xml

coverage-diff: coverage
	uv run --locked --all-packages --group test diff-cover coverage.xml \
		--compare-branch="$(BASE)" --fail-under=100

flaky-python:
	uv run --locked --all-packages qg-python-flaky --base "$(BASE)" --attempts 3

mutation:
	PYTHONPATH="$(CURDIR)/mutants/packages/core/src:$(CURDIR)/mutants/packages/github/src:$(CURDIR)/mutants/packages/python/src:$(CURDIR)/mutants/apps/qg/src" \
		uv run --locked --all-packages --group mutation mutmut run --max-children 1
	uv run --locked --all-packages --group mutation mutmut export-cicd-stats
	uv run --locked --all-packages python scripts/mutation_gate.py mutants/mutmut-cicd-stats.json

mutation-diff:
	@if git diff --quiet "$(BASE)...HEAD" -- packages/github/src/qg_github/compiler.py \
		packages/github/src/qg_github/commands.py packages/core/src/quality_graph_core/graph.py \
		packages/core/src/quality_graph_core/policy.py packages/core/src/quality_graph_core/result.py \
		packages/python/src/qg_python; then \
		echo "No changed mutation-gated decision modules"; \
	else \
		$(MAKE) mutation; \
	fi

audit: tools
	@requirements=$$(mktemp); \
	uv export --locked --package quality-graph-github --no-dev --no-hashes --format requirements-txt -o "$$requirements"; \
	uv run --locked --all-packages --group audit pip-audit -r "$$requirements"; status=$$?; \
	rm -f "$$requirements"; exit $$status
	"$(TOOLS_BIN)/gitleaks" detect --no-banner --redact

package:
	rm -rf build dist
	uv build --all-packages --out-dir dist
	uv run --locked --all-packages --group package twine check dist/*
	uv run --locked --all-packages --group package check-wheel-contents dist/*.whl
	uv run --isolated --no-project --with dist/quality_graph_core-*-py3-none-any.whl \
		--with dist/quality_graph_github-*-py3-none-any.whl \
		--with dist/quality_graph_cli-*-py3-none-any.whl qg --version
	uv run --isolated --no-project --with dist/quality_graph_core-*-py3-none-any.whl \
		--with dist/quality_graph_cli-*-py3-none-any.whl qg result schema >/dev/null
	uv run --isolated --no-project --with dist/quality_graph_python-*-py3-none-any.whl \
		qg-python-time-bombs --help >/dev/null
	@error=$$(mktemp); \
	if uv run --isolated --no-project --with dist/quality_graph_core-*-py3-none-any.whl \
		--with dist/quality_graph_cli-*-py3-none-any.whl qg validate 2>"$$error"; then \
		echo "CLI unexpectedly loaded a provider-free installation"; rm -f "$$error"; exit 1; \
	fi; \
	grep -q "uv tool install quality-graph-cli==0.1.2 --with quality-graph-github==0.1.2" \
		"$$error"; status=$$?; \
	rm -f "$$error"; exit $$status

check: fmt-check python-suppressions python-object-annotations python-triple-quotes \
	python-no-comments lint type analyze python-time-bombs coverage coverage-diff \
	flaky-python mutation-diff audit package

clean:
	@root=$$(git rev-parse --show-toplevel); \
	for path in .coverage htmlcov coverage.json coverage.xml coverage-report build dist mutants reports .tools; do \
		[ -e "$$path" ] || continue; \
		resolved=$$($(PYTHON) -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$$path"); \
		case "$$resolved" in "$$root"/*) rm -rf -- "$$path" ;; *) echo "Refusing to remove $$path"; exit 1 ;; esac; \
	done
