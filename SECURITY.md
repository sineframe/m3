# Security policy

## Reporting a vulnerability

Report it privately on GitHub through
[Report a vulnerability](https://github.com/sineframe/m3/security/advisories/new),
or email [hello@sineframe.com](mailto:hello@sineframe.com) with "security" in
the subject. Please don't open a public issue or pull request for it.

Include what you found, the M3 version (`m3 --version`), and the steps or a
test that shows it. We will reply as soon as possible, keep you updated
while we fix it, and credit you in the release notes unless you'd rather we
didn't.

## Supported versions

M3 is pre-1.0. Fixes go into the next release only, so upgrade to the latest
version before reporting:

```sh
uv tool upgrade sf-m3-cli
```

## What to report

Things we want to hear about:

- A credential leaking to a process that shouldn't get it. For example, a
  model-provider key reaching an MCP server, or `M3_ACCESS_TOKEN` reaching the
  pytest child process or an agent.
- A credential written to run history, traces, logs or an uploaded run.
- An agent getting tools or permissions beyond what the test configured.
- The local viewer or API being reachable from another machine, or usable
  without its access token.
- A release artifact that contains a secret or code that isn't in this
  repository.
- Problems with `m3 auth login`, saved CLI credentials or run uploads.

## How M3 runs your tests

Some behavior is by design. Knowing it should help you decide what to run.

- Agent tests start real agent harnesses on your machine. M3 limits which tools
  an agent gets, but it is not a sandbox. Run servers and agents you don't
  trust inside a container or VM.
- An agent or MCP server gets every credential you map to it, and can use or
  send that credential however it likes. Map only what each process needs. See
  [Configure credentials](https://m3.sineframe.com/docs/guides/credentials).
- The CLI loads `.env` from the project root. Don't commit it.
- Runs are only sent to M3 when you pass `--upload`. An uploaded run includes
  prompts, agent output and MCP traces. M3 refuses the upload if it finds a
  mapped credential value in it, but it can't recognize other private data, so
  don't upload runs whose tests use data you can't share.
