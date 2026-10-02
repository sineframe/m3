---
title: "Manage M3 access"
description: "Use device authorization for local uploads, or create and store a separate CI token for automated uploads."
---

# Manage M3 access

Choose the credential for where uploads run. Local commands can use a CLI
credential saved by `m3 auth login`. Automated CI jobs use a separately
created token through `M3_ACCESS_TOKEN`. These paths are independent: creating
a CI token does not require `m3 auth login`.

If `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` has a non-empty value, M3 requires
`M3_ACCESS_TOKEN` and does not read the interactive credential store.

## Requirements

For local CLI authorization:

- Install the M3 CLI on a computer where you can complete sign-in on the hosted
  authorization page.
- Use macOS Keychain, Windows Credential Manager, or a supported Linux Secret
  Service or KWallet credential store. `m3 auth login` stops before
  authorization when supported storage is unavailable.

For CI token creation, sign in to the hosted account console and choose a CI
secret store that can expose the token to the M3 process as
`M3_ACCESS_TOKEN`. Both paths require active membership in the target M3
organization. The hosted console provides organization creation and join paths
when you need membership.

## Authorize the local CLI

From any working directory, run:

```sh
m3 auth login
```

The CLI opens the hosted sign-in page. If it cannot open a browser, it prints a
page URL and code instead. Sign in, choose the organization, confirm the code,
and approve the authorization. The CLI receives a 30-day credential with
`kind=cli` and saves it in the operating-system credential store.

A completed authorization prints `M3 CLI credential saved in OS credential
store.` This message confirms that the CLI saved the credential. It does not
verify an upload or a CI token.

If you deny the request or the authorization expires, login exits without
saving a new credential. If you cancel the command, polling stops, but the
server authorization can remain active until it expires. Run `m3 auth login`
again to start a new authorization.

When reauthorization replaces a saved CLI credential, login revokes the old
credential after saving the new one. If that revocation fails, login exits with
status 2 and leaves the new credential saved. It keeps the old credential in
pending-revocation keyring state. A later login, status, or logout command
retries the old credential's revocation.

## Check the saved CLI credential

Run:

```sh
m3 auth status
```

The command validates the saved CLI credential with the control plane and
reports its token name, organization, token ID, and expiry. It requires network
access. If `M3_ACCESS_TOKEN` is also set, status validates its format and
reports its presence, but does not check that CI token with the control plane.

## Create a CI token

In the hosted token-management page, create a dedicated token with `kind=ci`
for the organization that will receive uploads. Copy the token when the page
shows it, then save it in your CI provider's secret store under the exact name
`M3_ACCESS_TOKEN`.

Configure the CI job so that the secret is present in the environment of the
`m3 ci test --upload` or `m3 upload` process. Keep model-provider credentials
and `M3_JUDGE_API_KEY` in separate secrets. M3 removes `M3_ACCESS_TOKEN` from
the pytest child environment, and credential mappings reject that exact name.
When `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` has a non-empty value, M3 requires
`M3_ACCESS_TOKEN` and does not fall back to the interactive credential store.
Without one of those markers, even `m3 ci test` can use the saved CLI
credential when `M3_ACCESS_TOKEN` is absent.

In every environment, an environment-provided `M3_ACCESS_TOKEN` takes
precedence over the saved CLI credential. An explicitly empty value also takes
precedence and causes a configuration error. Unset the variable when a local
command should use the saved CLI credential.

## End the local CLI session

From any working directory, run:

```sh
m3 auth logout
```

Logout revokes the saved `kind=cli` credential with the control plane and then
removes it from the operating-system credential store. If server revocation
fails, the command retains the local credential so you can retry. Logout does
not revoke, unset, or remove `M3_ACCESS_TOKEN`.

To rotate or remove CI access, create a replacement CI token, update the CI
secret, verify an upload, and revoke the old CI token in the hosted
token-management page.

Next, [run M3 in GitHub Actions](github-actions.md), or review the exact
[credential resolution rules](../../reference/credentials.md).
