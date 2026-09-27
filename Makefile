# Variables
PYTHON := python
MKDOCS := uvx --with mkdocs-material==9.7.7 mkdocs

.PHONY: setup dev-setup lint typecheck docs docs-build clean

# Install dependencies
setup:
	clear
	@echo "Setting up the project..."
	curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync



# Install dependencies + dev dependencies
dev-setup:
	@clear
	@echo "Setting up the development environment..."
	curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync --dev


lint:
	clear
	uv run ruff format
	uv run ruff check

typecheck:
	clear
	uv run mypy


# Preview the docs at http://127.0.0.1:8000 with live reload
docs:
	$(MKDOCS) serve

# Build the docs into site/ the way CI does
docs-build:
	$(MKDOCS) build --strict


# Remove the virtualenv, caches and build output
clean:
	find . -path ./.venv -prune -o -type d -name "__pycache__" -exec rm -rf {} +
	find . -path ./.venv -prune -o -type d -name "*.egg-info" -exec rm -rf {} +
	find . -path ./.venv -prune -o -type f -name "*.py[cod]" -delete
	rm -rf .venv site build dist htmlcov .coverage .pytest_cache .mypy_cache .ruff_cache .tox
