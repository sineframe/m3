# Get started with M3

Install the standalone `m3` command with uv:

```sh
uv tool install sf-m3-cli
```

From the project you want to test, initialize the M3 project files, install the
matching SDK, and check the setup:

```sh
cd my-project
m3 init
m3 setup
m3 doctor
```

Replace the skipped starter test created by `m3 init` with a real assertion,
then run it:

```sh
m3 test -- tests/test_m3_starter.py
```

Set provider credentials in the environment or pass an env file explicitly
with `--env-file .env`. M3 does not load `.env` automatically. See the [CLI
commands](/cli/commands) for options, [harness runtimes](/cli/harnesses)
for agent setup, and [results and UI](/cli/results-and-ui) for saved runs.
For direct Python testing, continue to the [SDK quick start](/sdk/quick-start).
