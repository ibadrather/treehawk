# Variables
PYTHON := python
MKDOCS := uvx --with mkdocs-material==9.7.7 mkdocs

.PHONY: setup dev-setup lint typecheck docs docs-build clean


setup:
	@clear 2>/dev/null || true
	@echo "Setting up the project..."
	curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync


dev-setup:
	@clear 2>/dev/null || true
	@echo "Setting up the development environment..."
	curl -LsSf https://astral.sh/uv/install.sh | sh
	uv sync --dev


lint:
	@clear 2>/dev/null || true
	uv run ruff format
	uv run ruff check


typecheck:
	@clear 2>/dev/null || true
	uv run ty check --python-version "$$(uv run python -c 'import sys; print(*sys.version_info[:2], sep=".")')"


docs-serve:
	$(MKDOCS) serve
	$(MKDOCS) build --strict


clean:
	find . -path ./.venv -prune -o -type d -name "__pycache__" -exec rm -rf {} +
	find . -path ./.venv -prune -o -type d -name "*.egg-info" -exec rm -rf {} +
	find . -path ./.venv -prune -o -type f -name "*.py[cod]" -delete
	rm -rf .venv site build dist htmlcov .coverage .pytest_cache .ruff_cache .mypy_cache .tox
