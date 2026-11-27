.DEFAULT_GOAL := help
.PHONY: help setup test coverage build clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install dependencies (including dev group)
	uv sync

test: ## Run the test suite (no coverage)
	uv run pytest --no-cov

coverage: ## Run tests and open the HTML coverage report
	uv run pytest --cov-report=html
	@uv run python -c "import pathlib, webbrowser; webbrowser.open(pathlib.Path('htmlcov/index.html').resolve().as_uri())"

build: ## Build sdist and wheel into dist/
	uv build

clean: ## Remove build artifacts and caches
	rm -rf dist htmlcov .pytest_cache .coverage
	find . -type d -name __pycache__ -not -path "./.venv/*" -prune -exec rm -rf {} +
