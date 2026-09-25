# CI and browser authentication security review

This review covers the control-plane sign-in page, CLI handoff, credential handling, and
report upload. It was performed against the implementation in this branch.

| Boundary | Finding | Resolution and verification |
| --- | --- | --- |
| Browser sign-in | Provider-specific code and ID tokens would couple the CLI to Firebase. | Serve the temporary page from the control plane's HTTPS origin. The browser uses the existing session, organization, and token APIs; the CLI contains no Firebase code or config. Browser session cookies and CSRF values never enter the CLI. |
| Browser to CLI callback | A redirect can be intercepted or forged locally. | Bind only `127.0.0.1` on an ephemeral port, validate callback path and random state, accept only a short-lived one-time code, and bind its HTTPS exchange with PKCE S256. No PAT appears in the URL. |
| Code redemption | Concurrent or replayed exchanges could mint extra PATs. | Persist grants with expiry and atomically consume a grant with any optional developer-token issuance; reject a second exchange. Signing in to manage CI tokens need not mint a developer token. |
| Public exchange endpoint | Random, well-formed codes could otherwise trigger billable Firestore reads. | CLI grants now contain a truncated HMAC; the control plane verifies it before a database lookup. CLI routes are off by default and require a shared signing secret when enabled. A stolen valid code can still trigger reads until it expires, so monitor exchange traffic and use an upstream abuse control if needed. |
| Abandoned grants | The browser or CLI may close after authorization but before code exchange, leaving a grant record. | Configure Firestore TTL on `cli_login_grants.expires_at`; also check expiry on every exchange because TTL deletion is not immediate. |
| Token issuance | CI tokens are visible once in the browser for copying into the CI provider; developer PATs are not. | Keep CI token creation explicit and return developer PATs only to the CLI over the HTTPS exchange. Token names distinguish use, but the current control plane does not enforce different privileges for developer and CI PATs. |
| Developer credential storage | A positive keyring priority alone does not prove secure OS storage. | Accept only supported OS credential backends; refuse developer-token issuance when secure storage is unavailable. Keep only token metadata in an owner-only local file. |
| CI secret scope | Test code can access inherited environment and same-user resources. | Remove `M3_ACCESS_TOKEN` from the pytest environment and reject mappings into harness/judge credentials. Keep the CI job restricted to trusted code. This is a reduction in accidental exposure, not a sandbox. |
| Report contents | Traces and pytest output may contain credentials. | Retain capture redaction, reject known resolved credential values in serialized summaries and execution bodies before network writes, and keep cache files owner-only. Unknown or transformed secrets remain a user responsibility. |
| Upload destination | A cached request could be reused for the wrong run or origin. | Validate run IDs before path construction, bind cache metadata to destination/run ID, validate cached identities, and reject redirects. Publish only finalized runs. |

For a later `m3 upload RUN_ID`, M3 records the names and keyed fingerprints of
resolved harness/judge credentials in the local run manifest. It requires the
same values before uploading, then scans the payload for them again. The
fingerprint key stays in the user's local config directory, outside `.m3/`
artifacts; a downloaded report alone cannot be safely uploaded from another
machine. The upload PAT may be rotated independently because it is excluded
from the test process and checked separately at publication.

Control-plane already checks PAT hashes, expiry, revocation, current membership
grant, and account state on each upload. Its browser sessions require verified
email, recent authentication, and CSRF protection. The browser page uses the
control-plane origin, so production CORS need not allow localhost.

The CLI sign-in page clears an existing browser session and requires fresh
authentication to issue a 10-minute session token. Selecting Done or Cancel
clears that session; closing the tab does not sign out immediately, so the
cookie may remain usable until the token expires.

Release validation must confirm that no Firebase code or Web config is packaged
into the CLI. The control-plane page still uses a public Firebase Web key;
restrict it to required Firebase APIs and review Auth quotas. Those live Google
Cloud settings were not accessible in this workspace and remain unverified. Production login
and grant-exchange smoke tests require an authorized test account. Do not
authorize `localhost` in production Firebase merely for email/password sign-in.
The control-plane CI job runs the emulator-backed grant tests, while a local
run without the emulators does not validate those paths. The browser bundle is
checked against a reproducible build in control-plane CI. Neither check
replaces a production smoke test or verification of Firestore TTL, Firebase
key restrictions, and Auth quotas.
