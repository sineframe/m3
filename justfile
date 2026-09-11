set shell := ["zsh", "-cu"]

# Exported as data for the guarded reset helper. This keeps the documented
# `just dev-db-reset CONFIRM=reset` form without interpolating the value into
# shell source.
export CONFIRM := ""

default:
    @just --list

setup install:
    uv sync --all-packages --all-extras --all-groups
    git config --local core.hooksPath .githooks

install-hooks:
    git config --local core.hooksPath .githooks

prepare-release VERSION:
    uv run --no-project --with packaging python scripts/prepare_release.py {{VERSION}}

api:
    uv run --project app uvicorn mcp_pal_app.main:app --reload

ui:
    uv run --project app --extra legacy-ui streamlit run app/src/mcp_pal_app/ui/app.py

test:
    PYTHONDONTWRITEBYTECODE=1 uv run --project sdk --extra pytest --group typecheck pytest -q sdk/tests
    PYTHONDONTWRITEBYTECODE=1 uv run --project app --extra legacy-ui --group test --group typecheck pytest -q app/tests
    PYTHONDONTWRITEBYTECODE=1 uv run --project cli pytest -q cli/tests

# Complete non-live suite used by the pre-push hook.
test-all:
    bash scripts/run_full_tests.sh

# Manual-only live OpenCode + browser merge gate. This performs one external
# provider call and may incur provider usage/cost; it is never a CI job.
live-ui-gate:
    uv run --env-file .env --project cli python scripts/live_ui_gate.py

test-unit:
    PYTHONDONTWRITEBYTECODE=1 uv run --project sdk --extra pytest --group typecheck pytest -q sdk/tests/unit
    PYTHONDONTWRITEBYTECODE=1 uv run --project app --group test --group typecheck pytest -q app/tests/unit

test-integration:
    PYTHONDONTWRITEBYTECODE=1 uv run --project sdk --extra pytest pytest -q sdk/tests/integration
    PYTHONDONTWRITEBYTECODE=1 uv run --project app --group test pytest -q app/tests/integration

# Fresh v0.2 development schema reset. The exact confirmation is required;
# Invoke as `just CONFIRM=reset dev-db-reset`; the helper deletes only the
# configured app-owned SQLite file and sidecars.
dev-db-reset:
    uv run --project app python app/scripts/dev_db_reset.py

package-check:
    uv run --project sdk --all-extras python scripts/check_packaging.py

typecheck-sdk-usage:
    PYTHONDONTWRITEBYTECODE=1 uv run --locked --project sdk --extra pytest --group typecheck pytest -q sdk/tests/unit/test_typecheck_examples.py

typecheck-sdk-public:
    uv run --isolated --python 3.10 --locked --project sdk --group typecheck mypy --config-file sdk/pyproject.toml --strict sdk/src/mcp_pal/types.py sdk/src/mcp_pal/errors.py sdk/src/mcp_pal/policy.py sdk/src/mcp_pal/interaction_handlers.py sdk/src/mcp_pal/configuration.py

# This checks the complete SDK without a baseline and is required by CI.
typecheck-sdk:
    uv run --isolated --python 3.10 --locked --project sdk --group typecheck mypy --config-file sdk/pyproject.toml --strict sdk/src/mcp_pal

lint:
    uv run --group lint ruff check .

format:
    uv run --group lint ruff format .

format-check:
    uv run --group lint ruff format --check .

# Validate a local ACP manifest. Set MANIFEST to a JSON file (or - for stdin).
harness-validate MANIFEST="harness.json":
    uv run --project sdk mcp-pal-harness validate {{MANIFEST}} --check-local

# Run an explicitly requested protocol probe against a local manifest.
harness-probe MANIFEST="harness.json":
    uv run --project sdk mcp-pal-harness probe {{MANIFEST}} --kind protocol

# Run the opt-in one-turn full probe. MODE_ID and SESSION_CONFIG are the exact
# values advertised by the protocol probe (the defaults suit the reference kit).
harness-full-probe MANIFEST="harness.json" TRANSPORT="stdio" MODE_ID="default" SESSION_CONFIG="{}":
    uv run --project sdk mcp-pal-harness probe {{MANIFEST}} --kind full --transport {{TRANSPORT}} --mode-id {{MODE_ID}} --session-config '{{SESSION_CONFIG}}'

# Launch the direct reference bridge demonstration command shown in README.
reference-bridge:
    uv run --project sdk mcp-pal-reference-bridge --target uv --target-args-json '["run", "python", "-m", "mcp_pal.fixtures.structured_cli"]'

# Run the deterministic bridge demonstration without network access.
bridge-demo:
    uv run --project sdk mcp-pal-harness probe sdk/examples/reference-harness.json --kind full --transport stdio

compile:
    uv run --project sdk python -m compileall -q sdk/src
    uv run --project app python -m compileall -q app/src
    uv run --project app python -c 'from mcp_pal_app.main import app; print(app.title)'
    uv run --project cli python -m compileall -q cli/src
    uv run --project cli python -c 'import mcp_pal_cli; print(mcp_pal_cli.__name__)'

check: lint compile

diff-check:
    git diff --check

clean:
    find sdk/src sdk/tests app/src app/tests app/scripts scripts -type f -name '*.pyc' -delete
    find sdk/src sdk/tests app/src app/tests app/scripts scripts -type d -name __pycache__ -empty -delete
    find . -maxdepth 2 -type d -name .pytest_cache -prune -exec rm -rf {} +
    rm -f .coverage
