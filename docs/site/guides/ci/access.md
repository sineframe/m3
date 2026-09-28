# Manage access tokens

Run `m3 auth login` on a computer with a browser. The login page lets you
manage organization access and tokens; the CLI receives a one-use
authorization result and can save a developer token in the operating-system
credential store.

```sh
m3 auth login
m3 auth status
```

For CI, create a dedicated token and store it as `M3_ACCESS_TOKEN` in the CI
secret store. Keep model-provider keys and `M3_JUDGE_API_KEY` separate.

```sh
m3 auth logout
```

Logout removes the locally saved copy. It does not revoke other copies or sign
the browser out. Revoke a token through the token-management page when
it must stop working.
