PYTHON_SOURCES := src tests
PYTHON ?= python3
BASE ?= origin/main

.DEFAULT_GOAL := check

.PHONY: install schemas schemas-check graph-generate graph-validate fmt fmt-check lint type analyze test test-unit test-integration coverage \
	mutation mutation-diff audit package check clean

install:
	uv sync --locked --all-groups

schemas:
	uv run --locked qg result schema --output schemas/result-v0.schema.json

schemas-check:
	@schema=$$(mktemp); \
	uv run --locked qg result schema --output "$$schema"; \
	cmp schemas/result-v0.schema.json "$$schema"; status=$$?; \
	rm -f "$$schema"; exit $$status

graph-generate:
	uv run --locked qg generate

graph-validate:
	uv run --locked qg validate

fmt: schemas graph-generate
	uv run --locked --group format ruff check $(PYTHON_SOURCES) --fix
	uv run --locked --group format ruff format $(PYTHON_SOURCES)
	uv run --locked --group format mdformat README.md

fmt-check: schemas-check graph-validate
	uv run --locked --group format ruff check $(PYTHON_SOURCES)
	uv run --locked --group format ruff format --check $(PYTHON_SOURCES)
	uv run --locked --group format mdformat --check README.md

lint:
	uv run --locked --group lint ruff check $(PYTHON_SOURCES)
	@files=$$(git ls-files '*.yaml' '*.yml'); \
	uv run --locked --group lint yamllint .yamllint.yaml $$files
	uv run --locked --group lint codespell README.md TASK.md src tests
	@files=$$(git ls-files '*.sh'); [ -z "$$files" ] || shellcheck $$files
	@files=$$(git ls-files '*.sh'); [ -z "$$files" ] || shfmt -d $$files
	@files=$$(git ls-files '.github/workflows/*.yaml' '.github/workflows/*.yml'); \
	[ -z "$$files" ] || actionlint -shellcheck= $$files

type:
	uv run --locked --group type mypy

analyze:
	uv run --locked --group analyze bandit -q -c pyproject.toml -r src/quality_graph
	uv run --locked --group analyze vulture
	semgrep --error --quiet --config p/python src/quality_graph

test: test-unit test-integration

test-unit:
	uv run --locked --group test pytest -q -m "not integration"

test-integration:
	uv run --locked --group test pytest -q -m integration

coverage:
	uv run --locked --group test pytest -q --cov=quality_graph --cov-branch --cov-report=term-missing

mutation:
	uv run --locked --group mutation mutmut run

mutation-diff:
	uv run --locked --group mutation mutmut run

audit:
	@requirements=$$(mktemp); \
	uv export --locked --no-dev --no-hashes --format requirements-txt -o "$$requirements"; \
	uv run --locked --group audit pip-audit -r "$$requirements"; status=$$?; \
	rm -f "$$requirements"; exit $$status
	gitleaks detect --no-banner --redact

package:
	rm -rf build dist
	uv build
	uv run --isolated --no-project --with dist/*.whl qg --version

check: fmt-check lint type analyze coverage audit package

clean:
	@root=$$(git rev-parse --show-toplevel); \
	for path in .coverage htmlcov coverage.json coverage-report build dist mutants reports; do \
		[ -e "$$path" ] || continue; \
		resolved=$$($(PYTHON) -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$$path"); \
		case "$$resolved" in "$$root"/*) rm -rf -- "$$path" ;; *) echo "Refusing to remove $$path"; exit 1 ;; esac; \
	done
