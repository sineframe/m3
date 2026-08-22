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

# Validate a local ACP manifest. Set MANIFEST to a JSON file (or - for stdin).
harness-validate MANIFEST="harness.json":
    uv run mcp-pal-harness validate {{MANIFEST}} --check-local

# Run an explicitly requested protocol probe against a local manifest.
harness-probe MANIFEST="harness.json":
    uv run mcp-pal-harness probe {{MANIFEST}} --kind protocol

# Run the opt-in one-turn full probe. MODE_ID and SESSION_CONFIG are the exact
# values advertised by the protocol probe (the defaults suit the reference kit).
harness-full-probe MANIFEST="harness.json" TRANSPORT="stdio" MODE_ID="default" SESSION_CONFIG="{}":
    uv run mcp-pal-harness probe {{MANIFEST}} --kind full --transport {{TRANSPORT}} --mode-id {{MODE_ID}} --session-config '{{SESSION_CONFIG}}'

# Launch the direct reference bridge demonstration command shown in README.
reference-bridge:
    uv run mcp-pal-reference-bridge --target uv --target-args-json '["run", "python", "-m", "mcp_pal.fixtures.structured_cli"]'

# Run the deterministic bridge demonstration without network access.
bridge-demo:
    uv run mcp-pal-harness probe examples/reference-harness.json --kind full --transport stdio

compile:
    uv run python -m compileall -q src
    uv run python -c 'from mcp_pal.main import app; print(app.title)'

check: compile

diff-check:
    git diff --check

clean:
    find src tests -type f -name '*.pyc' -delete
    find src tests -type d -name __pycache__ -empty -delete
    find . -maxdepth 2 -type d -name .pytest_cache -prune -exec rm -rf {} +
    rm -f .coverage
