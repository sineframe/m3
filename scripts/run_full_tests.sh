#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

export PYTHONDONTWRITEBYTECODE=1
export UV_PROJECT_ENVIRONMENT="$repo_root/.venv"
export ANTHROPIC_API_KEY=""
export OPENAI_API_KEY=""
export OPENROUTER_API_KEY=""
export OPENCODE_API_KEY=""

uv sync --locked --all-packages \
  --extra pytest --extra judge --extra storage --extra property \
  --group test --group typecheck

uv run --locked --no-sync --project sdk \
  pytest -q -n 2 --dist worksteal \
  -m "not live and not process_lifecycle" sdk/tests
uv run --locked --no-sync --project sdk \
  pytest -q -m "not live and process_lifecycle" sdk/tests
uv run --locked --no-sync --project sdk \
  pytest -q -n 2 --dist worksteal -m "not live" sdk/examples/tests
uv run --locked --no-sync --project app \
  pytest -q -n 2 --dist worksteal -m "not live" app/tests
uv run --locked --no-sync --project cli pytest -q cli/tests
