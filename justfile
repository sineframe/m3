set shell := ["zsh", "-cu"]

default:
    @just --list

setup install:
    uv sync --extra dev

api:
    uv run uvicorn mcp_pal.main:app --reload

ui:
    uv run streamlit run src/mcp_pal/ui/app.py

test:
    uv run pytest -q

test-unit:
    uv run pytest -q tests/unit

test-integration:
    uv run pytest -q tests/integration

compile:
    uv run python -m compileall -q src
    uv run python -c 'from mcp_pal.main import app; print(app.title)'

check: compile

clean:
    find src tests -type f -name '*.pyc' -delete
    find src tests -type d -name __pycache__ -empty -delete
    find . -maxdepth 2 -type d -name .pytest_cache -prune -exec rm -rf {} +
    rm -f .coverage
