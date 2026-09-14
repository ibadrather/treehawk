# Variables
PYTHON := python

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


# Clean Python cache files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -r .venv