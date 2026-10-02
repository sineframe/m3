<!-- Generated from docs/site/guides/ci/github-actions.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Run M3 in GitHub Actions

Use these workflows in a consumer repository whose `pyproject.toml` and `uv.lock` include `sf-m3[pytest,storage,judge]`. The first runs without credentials for pull requests. The optional upload workflow is limited to trusted pushes to `main` and manual dispatch; secrets enter only in the final test step.

## Requirements

The consumer repository needs a locked uv project, a `.python-version` file, and tests under `tests/`. The workflows below use pinned actions, read-only repository permissions, and disable checkout credential persistence.

The CLI requires the `pytest`, `storage`, and `judge` extras in the project environment, even when a test does not use an LLM judge. `storage` supplies the SQLAlchemy dependency for the SQLite execution store that records the run. Add these extras to the project and update its lockfile before running the workflow.

For uploads, the repository must also contain a committed `m3.toml`. Run `m3 init` locally from the repository root to generate it, then commit the file before enabling the upload workflow. Keep its `project_id` stable, including when renaming the project. Do not run `m3 init` in CI or generate a new identity for each run; publishing rejects runs without a valid project identity.

## Credential-free pull request workflow

Save this workflow as `.github/workflows/m3.yml`:

```yaml
name: M3 tests

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  m3-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9
        with:
          python-version-file: .python-version
      - run: uv sync --locked
      - run: uv tool install "sf-m3-cli==$(uv run --locked --no-sync python -c 'from importlib.metadata import version; print(version("sf-m3"))')"
      - run: m3 setup --python .venv/bin/python
      - run: m3 ci test --python .venv/bin/python -- tests/ -q
```

## Optional trusted upload workflow

Configure repository secrets `M3_ACCESS_TOKEN`, `MY_AGENT_KEY`, and `MY_JUDGE_KEY`. Configure a repository variable `M3_AGENT_MODEL` with a model name available to the Codex harness; it is not a credential. Save this complete workflow as `.github/workflows/m3-upload.yml`:

```yaml
name: M3 uploaded tests

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

env:
  M3_AGENT_MODEL: ${{ vars.M3_AGENT_MODEL }}

jobs:
  m3-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9
        with:
          python-version-file: .python-version
      - run: uv sync --locked
      - run: uv tool install "sf-m3-cli==$(uv run --locked --no-sync python -c 'from importlib.metadata import version; print(version("sf-m3"))')"
      - run: m3 setup --python .venv/bin/python
      - name: Run agent tests and upload the run
        env:
          M3_ACCESS_TOKEN: ${{ secrets.M3_ACCESS_TOKEN }}
          MY_AGENT_KEY: ${{ secrets.MY_AGENT_KEY }}
          MY_JUDGE_KEY: ${{ secrets.MY_JUDGE_KEY }}
        run: >-
          m3 ci test --upload --python .venv/bin/python
          --harness "codex=$M3_AGENT_MODEL"
          --credential-env codex:OPENAI_API_KEY=MY_AGENT_KEY
          --credential-env judge:M3_JUDGE_API_KEY=MY_JUDGE_KEY
          -- tests/ -q
```

`uv sync --locked` installs the consumer project's locked dependencies without synchronizing unrelated workspace packages. The CLI version matches the locked SDK metadata. `m3 setup` and all installation steps run before the final step supplies credentials. The final command uses the project's `.venv` explicitly and passes only variable names in the credential mappings.

See [Configure credentials](guides-credentials.md) for other credential paths.
