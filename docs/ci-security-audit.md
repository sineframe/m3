# CI and browser authentication security review

This review covers the control-plane sign-in page, CLI handoff, credential handling, and
report upload. It was performed against the implementation in this branch.

| Boundary | Finding | Resolution and verification |
| --- | --- | --- |
| Browser sign-in | Provider-specific credentials or SDKs in the CLI would couple it to one identity provider and expose tokens to a second client. | Serve the temporary page from the control plane's HTTPS origin. The page uses the Supabase browser SDK and existing M3 organization/token APIs. The CLI contains no Supabase or Firebase SDK, project URL/key, browser session, ID token, or refresh token. |
| Browser to CLI callback | A redirect can be intercepted or forged locally. | Bind only `127.0.0.1` on an ephemeral port, validate callback path and random state, accept only a short-lived one-time code, and bind its HTTPS exchange with PKCE S256. No PAT appears in the URL. |
| Code redemption | Concurrent or replayed exchanges could mint extra PATs. | Persist grants with expiry and atomically consume a grant with any optional developer-token issuance; reject a second exchange. Signing in to manage CI tokens need not mint a developer token. |
| Public exchange endpoint | Random, well-formed codes could otherwise trigger billable database reads; replay or forged codes could create PATs. | Verify the truncated HMAC before database lookup; CLI routes require the existing shared signing secret. Store only a hash of a random one-use code in Supabase Postgres, bind it to the exact redirect URI and PKCE challenge, check expiry and the current Auth session, and consume it in the same transaction as optional PAT issuance. |
| Abandoned grants | The browser or CLI may close after authorization but before code exchange, leaving an unused grant. | Check expiry on every exchange. Expired grants are invalid regardless of when database cleanup removes them. |
| Token issuance | CI tokens are visible once in the browser for copying into the CI provider; developer PATs are not. | Keep CI token creation explicit and return developer PATs only to the CLI over the HTTPS exchange. Token names distinguish use, but the current control plane does not enforce different privileges for developer and CI PATs. |
| Developer credential storage | A positive keyring priority alone does not prove secure OS storage. | Accept only supported OS credential backends; refuse developer-token issuance when secure storage is unavailable. Keep only token metadata in an owner-only local file. |
| CI secret scope | Test code can access inherited environment and same-user resources. | Remove `M3_ACCESS_TOKEN` from the pytest environment and reject mappings into harness/judge credentials. Keep the CI job restricted to trusted code. This is a reduction in accidental exposure, not a sandbox. |
| Report contents | Traces and pytest output may contain credentials. | Retain capture redaction, reject known resolved credential values in serialized summaries and execution bodies before network writes, and keep cache files owner-only. Unknown or transformed secrets remain a user responsibility. |
| Upload destination | A cached request could be reused for the wrong run or origin. | Validate run IDs before path construction, bind cache metadata to destination/run ID, validate cached identities, and reject redirects. Publish only finalized runs. |

For a later `m3 upload RUN_ID`, M3 inspects the exact serialized summary and
execution payloads against all recognized test-time credentials, including
mapped harness/judge sources. It stores only a payload digest, source names,
and a clean/unsafe result in the local run manifest. Publication refuses an
unsafe or changed payload and scans current credentials again before network
writes. Rotating an unrelated CI secret therefore does not prevent a safe
retry. The upload PAT remains separate from the test process and is checked
again at publication. Unknown or transformed secrets still require care from
the test author.

Control-plane already checks PAT hashes, expiry, revocation, current membership
grant, and account state on each upload. Browser API calls use bearer access
tokens; the server verifies the current account and session state. Recent
password authentication is required for CLI authorization grants and
destructive actions. The browser page uses the control-plane origin, so
production CORS need not allow localhost.

The CLI sign-in page requires password authentication within five minutes
before issuing a short-lived authorization grant. Selecting Done or Cancel ends the token
management flow but does not sign out the browser. Browser sign-out is a
separate action and does not delete the PAT saved in the CLI's OS credential
store; `m3 auth logout` removes only that local copy.

Release validation checks CLI provider-code/configuration markers and scans
every archive member in all three release wheels for recognizable Supabase
key prefixes and provider/browser JWT claims. Each wheel's dependency list
rejects Firebase/Supabase requirements, including extra- and platform-marked
requirements, to cover the CLI's SDK/application dependency chain. These are
static artifact checks, not arbitrary third-party dependency resolution or a
guarantee against obfuscated secrets. Production login and grant-exchange
smoke tests require an authorized test account. The local
SQL integration tests validate grant redemption and token persistence against
the Supabase schema; they do not replace a production smoke test or review of
hosted Auth redirect settings and email delivery.
