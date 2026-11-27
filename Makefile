.DEFAULT_GOAL := help
.PHONY: help setup test coverage build clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install dependencies (including dev group)
	uv sync

test: ## Run the test suite (no coverage)
	uv run pytest --no-cov

# The coverage options and the threshold live in pyproject.toml (pytest addopts).
coverage: ## Run tests and print coverage (the threshold is set in pyproject.toml)
	uv run pytest

build: ## Build sdist and wheel into dist/
	uv build

clean: ## Remove build artifacts and caches
	rm -rf dist .pytest_cache .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -prune -exec rm -rf {} +
