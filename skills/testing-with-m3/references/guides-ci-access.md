<!-- Generated from docs/site/guides/ci/access.md by scripts/render_skill_references.py. Edit the source page, then rerun the script. -->

# Manage M3 access

Choose the credential for where uploads run. Local commands can use a CLI
credential saved by `m3 auth login`. Automated CI jobs use a separately
created token through `M3_ACCESS_TOKEN`. These paths are independent: creating
a CI token does not require `m3 auth login`.

If `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` has a non-empty value, M3 requires
`M3_ACCESS_TOKEN` and does not read the interactive credential store.

## Requirements

For local CLI authorization:

- Install the M3 CLI on a computer with a browser so that you can complete
  sign-in in the M3 account console.
- Use macOS Keychain, Windows Credential Manager, or a supported Linux Secret
  Service or KWallet credential store. `m3 auth login` stops before
  authorization when supported storage is unavailable.

For CI token creation, you need a CI secret store that can expose the token to
the M3 process as `M3_ACCESS_TOKEN`.

Both paths require active membership in the M3 organization that will receive
uploads. If you have none, the M3 account console offers organization creation
and join paths after sign-in.

## Authorize the local CLI

From any working directory, run:

```sh
m3 auth login
```

The CLI opens sign-in in the M3 account console. If it cannot open a browser,
it prints a page URL and code instead. Sign in, choose the organization,
confirm the code, and approve the authorization. The CLI receives a 30-day CLI
credential and saves it in the operating-system credential store.

A completed authorization prints `M3 CLI credential saved in OS credential
store.` This message confirms that the CLI saved the credential. It does not
verify an upload or a CI token.

If you deny the request or the authorization expires, login exits without
saving a new credential. If you cancel the command, polling stops, but the
server authorization can remain active until it expires. Run `m3 auth login`
again to start a new authorization.

When reauthorization replaces a saved CLI credential, login revokes the old
credential after saving the new one. An old credential that has already expired
or been revoked needs no revocation. If revoking it fails for another reason,
login still succeeds and prints a warning. The old credential then stays valid
until it expires, or until you revoke it in the M3 account console.

## Check the saved CLI credential

Run:

```sh
m3 auth status
```

The command validates the saved CLI credential with M3 and reports its token
name, organization, token ID, and expiry. If the saved credential has expired
or been revoked, status says so; run `m3 auth login` to replace it. The command
requires network access. If `M3_ACCESS_TOKEN` is also set, status checks its
format and reports its presence, but does not validate that CI token with M3.

## Create a CI token

You do not need to run `m3 auth login` first.

1. Open the M3 account console at
   [https://auth.sineframe.com/account](https://auth.sineframe.com/account)
   and sign in.
2. Select the organization that will receive the CI uploads.
3. In that organization's navigation, open **CI tokens**. Its address has the
   form `https://auth.sineframe.com/orgs/<organization-id>/tokens`; the
   console fills in the organization ID.
4. Select **Create CI token**, choose an expiry of 7, 30, or 90 days, and
   create the token.
5. Copy the token immediately. The console shows the secret only once.
6. In your CI provider, save it as a secret named exactly `M3_ACCESS_TOKEN`.
   For GitHub Actions, add a repository secret under **Settings** >
   **Secrets and variables** > **Actions**.

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

Logout asks M3 to revoke the saved CLI credential, then removes it from the
operating-system credential store. A credential that has already expired or
been revoked is removed without error. If revocation fails for another reason,
the command keeps the local credential so you can retry. Logout does not
revoke, unset, or remove `M3_ACCESS_TOKEN`.

To rotate or remove CI access, create a replacement on the organization's
**CI tokens** page, update the `M3_ACCESS_TOKEN` secret, verify an upload, and
revoke the old CI token on that page.

Next, [run M3 in GitHub Actions](guides-ci-github-actions.md), or review the exact
[credential resolution rules](reference-credentials.md).
