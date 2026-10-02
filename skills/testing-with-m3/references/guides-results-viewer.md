<!-- Generated from docs/site/guides/results/viewer.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Open saved test results

The standalone CLI includes a local viewer for runs saved in the default
project database.

## Create and open a run

From the project root used in [Your first MCP test](getting-started.md):

```sh
m3 test --ui -- tests/test_m3_starter.py
```

After pytest finishes, the CLI prints a tokenized loopback URL and opens the
newly stored run. It remains running so the browser can load data. Press
Ctrl+C when finished.

To open existing history without running tests:

```sh
m3 ui
```

Run `m3 ui` from the directory containing `.m3/executions.sqlite`. It currently
uses that default path; a database selected with `m3 test --results-db PATH`
cannot be supplied to `m3 ui`.
`m3 ui` prints a tokenized `/reports` link. It never creates a database or
searches parent directories, refuses missing or invalid history, and does not
accept `--project-root`.

The server binds to `127.0.0.1` and requires the launch token. Treat the
printed URL as a credential while the process is running. Do not expose the
viewer through a public reverse proxy.
