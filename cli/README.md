# M3 CLI

The `sf-m3-cli` package installs the `m3` command. It selects a project Python,
runs pytest, saves results, manages harness runtimes, and serves the local
viewer.

```sh
uv tool install sf-m3-cli
```

On macOS or Linux, you can use the hosted shell installer instead:

```sh
curl -LsSf https://m3.sineframe.com/install.sh | sh
```

Start with the [installation guide](https://m3.sineframe.com/docs/start/install)
and use the [CLI reference](https://m3.sineframe.com/docs/reference/cli/) for
commands and options.

The CLI and project SDK use separate environments and matching versions.
`m3 setup` installs the matching SDK into an isolated project environment; it
does not edit dependency manifests or install the CLI there.

## Develop the CLI

```sh
just setup
uv run --project cli --group test pytest cli/tests
```

Release procedures are in [Releasing M3](https://github.com/sineframe/m3/blob/main/docs/releasing.md).
