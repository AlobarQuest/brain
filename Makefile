.PHONY: install check test

install:
	uv pip install --python .venv/bin/python -r requirements-dev.txt

check:
	.venv/bin/ruff check src tests scripts
	.venv/bin/ruff format --check src tests scripts
	.venv/bin/pyright
	.venv/bin/pytest -q

test:
	.venv/bin/pytest -q
