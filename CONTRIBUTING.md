# Contributing to M3

Bug reports, fixes, docs and new harness support are all welcome. This page
covers how to get a working checkout and what a pull request needs before it
can merge.

By taking part you agree to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Before you start

For a bug, open an issue with the M3 version (`m3 --version`), the harness and
model you ran, and the smallest test that shows the problem. A failing test is
the most useful thing you can attach.

For anything bigger than a bug fix, such as a new harness, a public API change
or a new CLI command, open an issue first so we can agree on the shape before
you write it.

## Set up

You need [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

```sh
git clone git@github.com:sineframe/m3.git
cd m3
just setup
```

`just setup` syncs every workspace package and installs the pre-push hook,
which runs Ruff and mypy before each push. `just --list` shows the other
recipes.

The repository is a uv workspace with three packages:

- `sdk/`: the `m3` Python package and pytest plugin. Execution and result
  behavior belongs here.
- `app/`: the internal API that stores runs and serves the viewer.
- `cli/`: the `m3` command. It is optional for SDK users.

Read [Architecture](docs/architecture.md) before changing anything that
crosses those boundaries.

## Make a change

Keep a pull request to one change. A fix and the refactor it made easier are
two pull requests.

Add or update tests with the change. For a bug fix, the test should fail
without the fix. Run the tests for the area you touched rather than the whole
suite; it is slow, and CI runs all of it:

```sh
uv run --project sdk --extra pytest pytest sdk/tests/unit/test_configuration.py
uv run --project cli --group test pytest cli/tests
```

Tests marked `live` call real model providers. They need credentials and cost
money, so they never run by default and are not required for a pull request.

Before you push, run the same checks as the pre-push hook:

```sh
just lint
just format-check
just typecheck-production
```

`just format` fixes formatting in place.

## Documentation

User documentation lives in `docs/site`. Read the
[documentation writing guide](docs/documentation-writing-guide.md) before
changing it. Runnable guide examples live under `sdk/examples/docs` and are
checked in CI, so a broken example fails the build.

If you change docs, examples, the CLI reference or the public Python API, run
the docs checks:

```sh
python3 scripts/render_docs_examples.py --check
python3 scripts/render_docs_navigation.py --check
python3 scripts/validate_docs_site.py
python3 scripts/validate_docs_examples.py
python3 scripts/render_skill_references.py --check
.venv/bin/python scripts/render_docs_api_reference.py --check
```

A `--check` failure means a generated file is out of date. Run the same script
without `--check` and commit the result.

To preview the full site, build it from the landing repository:

```sh
M3_DOCS_DIR=/path/to/m3 npm run build
```

## Pull requests

- Say what changed and why. Link the issue if there is one.
- Say how you tested it. If you ran a live harness, name it and the model.
- Call out anything user-visible: a changed default, a renamed option, new
  output.
- CI must pass. If a test fails for reasons unrelated to your change, say so
  in the pull request instead of retrying until it goes green.

A maintainer will review and merge. Releases are cut separately; see
[Releasing](docs/releasing.md).

## License

M3 is licensed under [Apache-2.0](LICENSE). Contributions are accepted under
the same license.
