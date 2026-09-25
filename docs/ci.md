# CI tests and report publishing

`m3 ci test` uses the same project Python, harness selection, storage, and
feedback as `m3 test`. It excludes tests marked `pytest.mark.m3(ci=False)`.
The nearest explicit `ci` value wins, so a test can use `ci=True` to override
a module default. An ordinary `m3 test` still runs these tests. Pytest paths,
`-k`, `-m`, and M3 `--suite` selectors continue to combine normally.

```python
import pytest

pytestmark = pytest.mark.m3(suite_name="shipping")

@pytest.mark.m3(ci=False)
def test_requires_a_person_at_the_keyboard():
    ...
```

Run locally with provider keys from the environment or an explicitly selected
file:

```sh
m3 init
m3 setup
m3 ci test --env-file .env --harness codex=gpt-5.6-sol -- tests/
```

M3 never discovers `.env` automatically. Ambient variables win over values in
`--env-file`, and file values are not interpolated. The file may contain the
keys used by the test harness, `M3_JUDGE_API_KEY` for `LLMJudge`, and
`M3_ACCESS_TOKEN` for publishing. Keep the file outside version control. An
empty ambient variable also takes precedence, so unset it if the file should
supply that value. `--credential-env codex:OPENAI_API_KEY=CI_AGENT_KEY` and
`--credential-env judge:M3_JUDGE_API_KEY=CI_JUDGE_KEY` map custom CI secret
names to their consumers. M3 does not use a harness key as a judge fallback.

## Sign in and manage tokens

Run `m3 auth login`. The CLI opens a temporary control-plane page in your
browser. Sign in, choose or create an organization, then choose any of these
actions:

- Request a 30-day developer token for this computer; the CLI saves it in the
  OS credential store after you select Done.
- Create a named CI token with a supported expiration and copy it once into
  your CI provider's secret store.
- List or revoke your tokens.

Select **Done** when finished. The page sends a short-lived, one-use code to
the CLI on the same computer; the CLI exchanges it over HTTPS. An M3 bearer
token is never placed in the browser redirect URL. Signing in alone does not
issue a developer token. Creating a new developer token does not revoke an
older one; revoke old tokens explicitly when they should stop working.
`/cli/login` clears any existing control-plane browser session and requires a
fresh sign-in before issuing a 10-minute session. Done and Cancel clear that
session; closing the tab may leave it active until expiry. `m3 auth status`
reports the saved developer token's ID, which you can match in the sign-in
page before revoking an older token; `m3 auth logout` removes it locally.

The CLI contains no Firebase SDK or Firebase configuration. Authentication
provider changes are owned by the control plane. The browser and CLI must be
on the same computer for the loopback callback; noninteractive CI uses a
separately created `M3_ACCESS_TOKEN` instead of browser sign-in.

## Publish a run

`--upload` is explicit. Without it, CI tests remain local and need no M3
account. With it, M3 publishes completed passing and failing reports:

```sh
m3 ci test --env-file .env --upload -- tests/
```

If the network or control plane fails, the local run remains available. Use
the printed run ID with `m3 upload RUN_ID` to retry without rerunning tests.
When credentials came from an env file, provide it again with
`m3 upload RUN_ID --env-file .env` if the current upload token or provider
credentials are there. M3 inspects the exact report payloads against
test-time credentials after the test, recording only a digest and a pass/fail
result in the local run manifest. A retry may use rotated credentials, but M3
refuses to publish if those payloads contained a test-time credential, have
changed since inspection, or contain a current credential. Older runs without
an inspection cannot be uploaded safely.
The default database is `.m3/executions.sqlite` and reports are saved under
`.m3/reports/RUN_ID`. A requested upload failure makes an otherwise passing
CI job fail. The hosted report viewer is planned separately.

In GitHub Actions, M3 records an allowlisted repository, commit, ref,
workflow, job, run attempt, and job URL with the feedback. For other CI
providers, use `--ci-metadata path/to/metadata.json`; explicit fields override
GitHub values. The JSON accepts only those fields plus `provider` and
`pr_number`. Matrix jobs publish separate M3 runs.

## GitHub Actions

Create a dedicated CI token using `m3 auth login` and store it as the
`M3_ACCESS_TOKEN` repository secret. Store provider and judge keys separately.
To rotate it, create a replacement in the sign-in page, update the CI secret,
confirm a run succeeds, and then revoke the old token there.
Replace the M3 version placeholder with a release that includes `m3 ci test`.
Install project dependencies and the selected harness before running tests.

```yaml
name: M3 CI
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  m3-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
        with:
          version: "0.12.7"
          python-version: "3.11"
      - run: uv tool install 'sf-m3-cli==<release-with-m3-ci-test>'
      - run: |
          uv sync --locked
          m3 setup
          npm install --global @openai/codex@0.156.1
      - name: Execute and publish
        env:
          OPENAI_API_KEY: ${{ secrets.M3_AGENT_OPENAI_KEY }}
          M3_JUDGE_API_KEY: ${{ secrets.M3_JUDGE_KEY }}
          M3_ACCESS_TOKEN: ${{ secrets.M3_ACCESS_TOKEN }}
        run: m3 ci test --upload --harness codex=gpt-5.6-sol -- tests/
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2
        if: always()
        with:
          name: m3-results
          path: .m3/
          if-no-files-found: ignore
```

The example is for trusted branches. A fork pull request receives no M3 or
provider secrets; run only tests that need no secrets in that workflow. Do not
run fork code in a privileged workflow with these secrets. M3's removal of
`M3_ACCESS_TOKEN` from the pytest environment limits accidental disclosure,
but tests running as the same OS user are not a security sandbox.
The [CI security review](ci-security-audit.md) records the authentication,
credential, and report-upload boundaries and release checks.

## Failures and limits

An expired or revoked access token must be replaced. Publishing requires a
valid `m3.toml` project identity and a completed run. The control plane accepts
at most 1 MiB for the summary and 16 MiB for each execution report. M3 checks
known resolved credentials before upload; user-generated output may still
contain other sensitive material, so review captured data before publishing.
