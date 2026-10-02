# CI and device authentication security review

This review is for maintainers checking the boundary between the M3 CLI, the
hosted account console, the control plane, the operating-system credential
store, and CI. It covers the implementation in M3 commit `54e698d`, the merged
control-plane PR
[`sineframe/control-plane#15`](https://github.com/sineframe/control-plane/pull/15),
and the merged console PR
[`rishhavv/m3-ui#62`](https://github.com/rishhavv/m3-ui/pull/62).

The reported executed evidence covers CLI request and response validation,
credential storage behavior, console state handling, and non-database
control-plane checks. Supabase-backed authorization test code exists, but
control-plane PR #15 records that its database-backed execution was unavailable
in that review environment. A live hosted device authorization was not run for
this review.

## Authentication boundaries

| Boundary | Implemented behavior | Evidence and limit |
| --- | --- | --- |
| Hosted sign-in | `m3 auth login` asks the control plane to start device authorization, then opens the separately deployed account console. The console owns password and Google sign-in through the Supabase browser SDK. Provider access tokens, refresh tokens, project configuration, and SDK code do not enter the CLI. | The CLI calls the device endpoints in `cli/src/m3_cli/auth.py`. Console PR #62 adds `/cli/authorize` and its sign-in continuations. The control plane does not serve embedded console assets. |
| Device and user codes | The control plane returns a random bearer `device_code`, a short human `user_code`, a sign-in URL, a complete sign-in URL, a 600-second lifetime, and a five-second polling interval. PostgreSQL stores only the SHA-256 hash of the device code. The browser receives the user code and safe device metadata, never the device code or a PAT. | Control-plane PR #15 tests the stored hash, browser response fields, expiry, denial, polling interval, and replay. Console PR #62 tests password sign-in continuation, approval, denial, and expiry with mocked APIs. Those browser tests are not live hosted evidence. |
| Approval binding | Approval requires a current Supabase bearer, authentication within five minutes, and an active membership in the selected organization. The control plane binds the approval to the verified Supabase UID, normalized email, session ID, organization ID, and membership grant. | The control-plane service owns these checks. Its Supabase-backed tests inspect the persisted binding. The CLI does not implement or bypass browser-session, account, or membership policy. |
| Poll-time authorization | The unauthenticated polling endpoint accepts the device code, looks up its hash, applies the polling interval, and returns the defined pending, slowdown, denial, expiry, or invalid-authorization errors. Before issuance, the control plane rechecks the bound account, Supabase session, organization membership, and exact membership grant. It consumes the authorization and creates one audited PAT in one transaction. | Control-plane PR #15 exercises concurrent polling, replay, revoked browser session, changed membership grant, and removed membership. These are backend guarantees, not CLI-side checks. |
| PAT kinds | A successful device authorization returns one 30-day PAT named `M3 CLI` with `kind=cli`. Organization token management creates copy-once `kind=ci` PATs with a 7-day, 30-day, or 90-day lifetime. Both kinds authorize uploads, but only a CLI PAT can inspect or revoke itself through the CLI session endpoint. Neither kind authorizes browser reads, device approval, token management, or organization administration. | The control-plane integration tests check token kind, name, lifetime, single issuance, upload scope, and rejection of a CI PAT by the CLI session endpoint. The console lists and creates only CI tokens. |
| Local CLI credential | Login probes for a supported OS credential backend before starting authorization. The CLI stores the CLI PAT in that backend under an account derived from the strict control-plane origin. Its owner-only metadata file contains the installation ID and non-secret token metadata, not the PAT. | Implementation inspection shows the explicit backend-type allowlist, write probe, and `0700` directory and `0600` file modes. `cli/tests/test_auth.py` covers metadata preservation, legacy keyring-account migration, concurrent installation-ID creation, corrupt metadata, a mocked preflight failure, and save-failure rollback. It does not exercise the real backend allowlist or file modes. |
| CI credential | `M3_ACCESS_TOKEN` is an environment-provided CI PAT. When `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` has a truthy value, an absent environment token is an error and the CLI does not fall back to the interactive keyring. Outside that condition, the environment token takes precedence over a saved CLI PAT. M3 removes it from the pytest child environment and rejects mappings into harness or judge credentials. | Implementation inspection establishes the three truthy marker checks. `cli/tests/test_ci_credentials.py` covers environment precedence, the truthy `CI` case, child-environment removal, and mapping rejection. `m3 auth logout` does not inspect, revoke, or remove `M3_ACCESS_TOKEN`. |
| Online status | `m3 auth status` validates the local format of `M3_ACCESS_TOKEN` when it is present, but does not check that CI PAT online. Separately, it reads the saved CLI PAT and calls the CLI session endpoint. It accepts only a response whose metadata says `kind=cli`, then reports safe token and organization metadata. | CLI tests cover a valid environment token, valid saved-CLI session metadata, malformed session metadata, and absence of either bearer from output. Implementation inspection establishes the environment token's strict local PAT-format check. The control plane returns metadata without the token or its stored hash. |
| Self-revocation and logout | `m3 auth logout` sends the saved CLI PAT to the control plane for self-revocation, then removes it from the OS credential store. A remote failure leaves the local credential in place so the command can be retried. Self-revocation is idempotent for an expired or already revoked CLI PAT. | CLI tests cover remote failure, successful local removal, and ignoring `M3_ACCESS_TOKEN`. Control-plane tests verify that the revoked PAT no longer authenticates. Browser sign-out and CI-token revocation are separate operations. |
| Re-login and an older credential | After a re-login issues a replacement, the CLI records the previous CLI PAT in a separate pending-revocation keyring entry before saving the new PAT. It then revokes the previous PAT. If that revocation fails, the new PAT remains saved, the command reports failure, and later login, status, or logout attempts the pending revocation again. If saving the replacement fails, the CLI keeps the previous PAT and attempts to revoke the newly issued PAT. | Implementation inspection establishes the pending-revocation failure and retry paths. `cli/tests/test_auth.py` covers successful replacement and the save-failure path that preserves the old PAT and attempts to revoke the new PAT; it does not exercise a pending-revocation failure or retry. The recovery record is secret keyring state, not local metadata. |
| URL and response validation | Both `M3_CONTROL_PLANE_URL` and `M3_AUTH_URL` must be HTTPS origins without credentials, paths, queries, fragments, ambiguous host syntax, or unsupported IPv6 literals. The CLI accepts returned verification URLs only on the configured auth origin and exact `/sign-in` route; the complete URL must contain exactly the expected encoded `/cli/authorize?code=USER_CODE` continuation. HTTP redirects are rejected and response bodies are capped at 64 KiB. Device responses must contain the required fields with the expected types. Issued-token metadata fields must be nonempty strings with `kind=cli`, and the bearer must match the strict M3 PAT format before storage. | `cli/tests/test_ci_credentials.py` covers strict origin normalization. `cli/tests/test_auth.py` covers exact continuation matching, redirect rejection, body limits, one valid device/token response, malformed status metadata, and suppression of device and PAT values in errors. The remaining response checks are established by implementation inspection. TLS and hosted routing still depend on the deployed origins. |
| Public endpoint rate limits | The control plane, not the CLI, limits device-authorization creation to 20 requests per minute per client IP and applies a 500 requests-per-minute per-instance ceiling. With no trusted header configured, it uses the socket address and ignores forwarding headers. A deployment may name one trusted client-IP header only when every request passes through a proxy that overwrites it; missing, duplicate, comma-separated, malformed, or non-IP values are rejected. | Control-plane PR #15 owns and tests client-IP extraction and both limits. Its Fly configuration trusts `Fly-Client-IP`. Operators own the proxy topology and must not configure a caller-controlled header. |
| Upload secret handling | The upload credential stays outside the test process. Before network writes, M3 checks the exact serialized summary and execution payloads against recognized test-time credential values. It binds cached upload data to the destination and run ID, rejects redirects, and keeps cache files owner-only. | CLI tests cover known-secret rejection, destination binding, finalized-run enforcement, and redirect rejection. Unknown or transformed secrets remain the test author's responsibility; this is not a process sandbox. |

## Release evidence

Release artifact checks scan every member of the SDK, application, and CLI
wheels for Firebase or Supabase code and configuration markers, recognizable
provider credentials, and PEM private keys. The wheel metadata also rejects
direct Firebase and Supabase requirements. These static checks do not resolve
arbitrary transitive packages and do not prove that an opaque or transformed
secret is absent.

The coordinated repositories report passing CLI unit tests, control-plane Go
tests, console unit tests, and a focused mocked-browser suite. The
control-plane PR records that its database-backed tests were not run in that
review environment, while its Supabase-backed test code remains present. The
console PR records that its broad end-to-end suite could not run without the
report backend. None of those automated results proves that the deployed
control plane, console routing, Supabase session policy, trusted proxy header,
OS keyring, upload service, or server-side revocation work together. Complete
the live smoke checklist in [Releasing M3](releasing.md) before claiming hosted
acceptance.
