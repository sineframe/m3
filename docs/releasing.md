# Releasing M3

M3 publishes a CLI whose interactive and CI credentials have separate
lifecycles. `m3 auth login` obtains a 30-day `kind=cli` PAT through the hosted
device authorization flow and saves it in a supported operating-system
credential store. The account console creates copy-once `kind=ci` PATs for
`M3_ACCESS_TOKEN`. When `CI`, `GITHUB_ACTIONS`, or `GITLAB_CI` is truthy, the
CLI does not read the interactive keyring. `m3 auth logout` does not act on
`M3_ACCESS_TOKEN`.

CLI releases must contain no Firebase or Supabase SDKs, provider configuration,
project URLs or keys, browser access or refresh tokens, or service credentials.
M3 PATs are the only credentials used for API uploads. Release verification
scans every archive member in the SDK, application, and CLI wheels for
Firebase or Supabase code and configuration markers, Google or Firebase API
keys, Supabase key prefixes, PEM private keys including service-account JSON,
and recognizable provider or browser JWT credentials. No wheel, directory,
filename extension, or metadata member is exempt.

All three wheels reject direct Firebase and Supabase requirements, including
extra-marked and platform-marked requirements, so an SDK or application wheel
cannot add one directly to a CLI installation. This policy does not resolve
arbitrary third-party dependency trees and cannot detect every obfuscated or
opaque credential. Never bundle a live credential.

## Coordinated authentication release smoke

Run this checklist when releasing the device authorization change or changing
its control-plane or account-console contract. The observable result is a
matched control-plane, console, and CLI release that can issue, validate, use,
and revoke a CLI credential without changing an independent CI credential.

### Requirements

- Deploy the control-plane revision containing
  [`sineframe/control-plane#15`](https://github.com/sineframe/control-plane/pull/15)
  with all migrations applied.
- Deploy the account-console revision containing
  [`rishhavv/m3-ui#62`](https://github.com/rishhavv/m3-ui/pull/62), including
  its `/cli/authorize` route and exact control-plane proxies.
- Stage the version-matched `sf-m3`, `sf-m3-app`, and `sf-m3-cli` candidate
  wheels and their resolved dependency wheels in one directory. Use an
  absolute path to the exact CLI wheel.
- Use a machine with `uv`, a POSIX shell, a supported OS credential store, and
  a browser. On Windows, translate the shell functions and environment
  handling to PowerShell without changing the credential boundaries.
- Use a controlled account with an active organization. Create a disposable CI
  token for that organization and inject it into the shell as
  `M3_ACCESS_TOKEN` through the operator's secret manager. Do not paste it into
  commands, history, logs, issue comments, or release evidence.

From the M3 repository root, configure the staged artifacts and the deterministic
smoke project:

```sh
export M3_RELEASE_DIST="${M3_RELEASE_DIST:?set the absolute staged-wheel directory}"
export M3_CLI_WHEEL="${M3_CLI_WHEEL:?set the absolute sf-m3-cli candidate wheel}"
export M3_SDK_WHEEL="${M3_SDK_WHEEL:?set the absolute sf-m3 candidate wheel}"
export M3_SMOKE_PROJECT="$PWD/sdk/examples/docs/first-test"

case "$M3_RELEASE_DIST:$M3_CLI_WHEEL:$M3_SDK_WHEEL:$M3_SMOKE_PROJECT" in
  /*:/*:/*:/*) ;;
  *) printf '%s\n' "release and smoke paths must be absolute" >&2; exit 2 ;;
esac
test -d "$M3_RELEASE_DIST"
test -f "$M3_CLI_WHEEL"
test -f "$M3_SDK_WHEEL"
test -f "$M3_SMOKE_PROJECT/tests/test_m3_starter.py"

m3_candidate() {
  uv run --isolated --no-project --no-index \
    --find-links "$M3_RELEASE_DIST" \
    --with "$M3_CLI_WHEEL" m3 "$@"
}

m3_local() {
  (
    unset M3_ACCESS_TOKEN
    m3_candidate "$@"
  )
}
```

The `--no-index` candidate wrapper prevents an `m3` executable or M3 wheel from
being substituted from a package index. `M3_RELEASE_DIST` must contain the
version-matched first-party wheels and every dependency wheel needed to create
the isolated candidate environment.

For a non-production deployment, set the two public origins explicitly:

```sh
export M3_CONTROL_PLANE_URL="${CONTROL_PLANE_ORIGIN:?set the deployed control-plane HTTPS origin}"
export M3_AUTH_URL="${AUTH_ORIGIN:?set the deployed account-console HTTPS origin}"
```

Both values must be strict HTTPS origins, with no path, query, fragment,
userinfo, or ambiguous host spelling. `M3_AUTH_URL` must exactly match the
control plane's deployed `CONSOLE_ORIGIN` after default HTTPS-port
normalization. `M3_CONTROL_PLANE_URL` must identify that console deployment's
matching control plane. The two origins do not need to use the same hostname.
Production uses the candidate's default origins unless the production
deployment contract says otherwise.

### Automated evidence before the live smoke

Run each command from a clean checkout of the named repository.

From the M3 repository root:

```sh
uv run --project cli --group test pytest \
  cli/tests/test_auth.py cli/tests/test_ci_credentials.py
```

From the control-plane repository root:

```sh
go test -count=1 ./...
go vet ./...
go build ./...
```

From the `m3-ui` repository root:

```sh
npm ci
npm test
npm run typecheck
npm run lint
npm run build
```

Record failures and environment-gated skips as limits. Passing mocked CLI,
handler, database, or browser tests is not evidence that hosted routing,
Supabase policy, trusted client-IP configuration, the local keyring, and
revocation work together.

### Live coordinated smoke

1. Confirm the control plane reports ready. Inspect deployment configuration
   without printing secrets. Confirm its console origin matches `M3_AUTH_URL`,
   its allowed browser origin includes that console, and its trusted client-IP
   header is supplied and overwritten by the actual edge proxy.

   ```sh
   curl --fail --silent --show-error \
     "${M3_CONTROL_PLANE_URL}/readyz" >/dev/null
   ```

2. Remove any old disposable local CLI credential. If logout reports a remote
   error, stop and resolve it. Do not treat deletion of keyring state by hand
   as revocation.

   ```sh
   m3_local auth logout
   ```

3. Prepare one uploadable run with the staged SDK wheel. The temporary project
   environment uses the exact candidate SDK artifact while resolving its
   third-party test dependencies normally:

   ```sh
   export M3_SMOKE_ENV="$(mktemp -d)/venv"
   uv venv "$M3_SMOKE_ENV"
   uv pip install --python "$M3_SMOKE_ENV/bin/python" \
     --find-links "$M3_RELEASE_DIST" \
     "$M3_SDK_WHEEL[pytest,storage]"

   M3_CI_OUTPUT="$(
     m3_local ci test \
       --project-root "$M3_SMOKE_PROJECT" \
       --python "$M3_SMOKE_ENV/bin/python" \
       -- tests/test_m3_starter.py
   )" || {
     printf '%s\n' "$M3_CI_OUTPUT"
     exit 1
   }
   printf '%s\n' "$M3_CI_OUTPUT"
   test "$(printf '%s\n' "$M3_CI_OUTPUT" | awk '/^Run ID: / { count++ } END { print count+0 }')" -eq 1
   export RUN_ID="$(
     printf '%s\n' "$M3_CI_OUTPUT" |
       awk -F ': ' '/^Run ID: / { print $2 }'
   )"
   test -n "$RUN_ID"
   ```

   Keep the default database and `.m3/reports` state under
   `M3_SMOKE_PROJECT`. Do not pass `--results-db`, move the report, or delete
   that state before both upload checks finish.

4. Run the interactive flow with no environment-provided CI token:

   ```sh
   m3_local auth login
   m3_local auth status
   m3_local upload "$RUN_ID" --project-root "$M3_SMOKE_PROJECT"
   ```

   In the browser, confirm that the sign-in page is on `M3_AUTH_URL`, the user
   code matches the terminal, the device and CLI metadata are expected, and
   the selected organization is correct before approving. Confirm that status
   validates the saved CLI credential online and that the upload completes.
   Do not record the user code, PAT, browser session, or response headers.

5. Use the exact candidate wheel to retain the saved CLI bearer only in memory,
   call logout, and require the same bearer to receive HTTP 401. The probe does
   not put the bearer in arguments, environment variables, files, or output:

   ```sh
   env -u M3_ACCESS_TOKEN uv run --isolated --no-project --no-index \
     --find-links "$M3_RELEASE_DIST" \
     --with "$M3_CLI_WHEEL" python - <<'PY'
   import urllib.error
   import urllib.request

   from m3_cli.auth import control_plane_url, load_saved_token, logout

   class NoRedirect(urllib.request.HTTPRedirectHandler):
       def redirect_request(self, *args, **kwargs):
           return None

   base = control_plane_url()
   token = load_saved_token(base)
   if token is None:
       raise SystemExit("no saved CLI credential before logout check")
   if logout() != 0:
       raise SystemExit("CLI logout failed")

   request = urllib.request.Request(
       base + "/v1/cli/session",
       headers={
           "Accept": "application/json",
           "Authorization": "Bearer " + token,
           "Cache-Control": "no-store",
       },
   )
   try:
       urllib.request.build_opener(NoRedirect()).open(
           request, timeout=20
       ).close()
   except urllib.error.HTTPError as response:
       status = response.code
       response.close()
       if status != 401:
           raise SystemExit(f"revocation probe returned HTTP {status}")
   except (urllib.error.URLError, TimeoutError, OSError):
       raise SystemExit("revocation probe could not reach the control plane") from None
   else:
       raise SystemExit("revoked CLI credential was still accepted")
   PY
   ```

6. Confirm local removal, then prove that the separate CI token still works:

   ```sh
   m3_local auth status
   test -n "${M3_ACCESS_TOKEN:-}"
   m3_candidate auth logout
   m3_candidate upload "$RUN_ID" --project-root "$M3_SMOKE_PROJECT"
   ```

   Status must report no local CLI credential. Logout with only
   `M3_ACCESS_TOKEN` present must not revoke or remove that environment
   credential. The repeated upload must complete with the CI token. Revoke the
   disposable CI token in the console after the smoke, unset
   `M3_ACCESS_TOKEN`, and remove `M3_SMOKE_ENV` without printing either secret.

Record the candidate versions, wheel hashes, source revisions, deployed
origins, OS, credential-store backend, identity provider, organization
fixture, commands, exit codes, and UTC date. Record only token IDs or redacted
metadata if needed, never a PAT, device code, browser credential, or CI secret.
If any live step was not run, state that exact limit. The automated suites and
the coordinated PR results do not constitute live hosted verification.

M3 publishes version matched `sf-m3`, `sf-m3-app`, and `sf-m3-cli` wheels to PyPI. The `m3` Python import and `m3` command remain stable public interfaces. The tag controls whether a GitHub Release is final or a prerelease: `v0.2.0` is final and `v0.3.0a1` is an alpha.

## Release from a tag

Prepare a final or prerelease tag from the reviewed default-branch commit and push it:

```sh
git tag -a v0.2.0 MAIN_COMMIT_SHA -m "Release v0.2.0"
git push origin v0.2.0
```

For an alpha, use a PEP 440 prerelease tag such as `v0.3.0a1`. The workflow builds the three matching wheels and installer, stages them in a draft GitHub Release, publishes the wheels to PyPI, and runs fresh install checks before publishing the GitHub Release.

Before creating the tag, render the combined landing site locally. From the landing repository root, set `M3_DOCS_DIR` to this M3 repository root and run `M3_DOCS_DIR=/path/to/m3 npm run build`; inspect the generated docs pages and navigation before tagging. This is the pre-tag render gate. The M3 release workflow starts only after a tag is pushed, so its source validator checks manifest consistency and links but cannot replace the rendered-site check.

`docs/site/navigation.json` keeps page `source` paths and navigation metadata;
it does not store routes. The landing renderer must derive routes from `source`:
`index.md` maps to `/`, a nested `dir/index.md` maps to `/dir/`, and a leaf
`dir/page.md` maps to `/dir/page`. Do not add a manifest `route` field or use
one as an independent route authority.

The tag-triggered M3 release does not deploy the documentation site. After the
tag's release workflow and GitHub Release succeed, update the docs pin in
`sineframe-landing` with `npm run docs:pin -- <tag>`, build from that public
tag, and open a landing pull request. Verify its Cloudflare preview before
merging; the existing landing Git integration deploys the new docs with the
landing site and publishes that release's `scripts/install-latest.sh` at
`https://m3.sineframe.com/install.sh`. Verify production after that merge. Do
not update the landing pin before the M3 release succeeds.


CI runs source checks and a fast test selection when a pull request is opened
or updated. A push to `main` runs the complete non-live suite, including SDK
process lifecycle tests serially, and compatibility checks. These results are
advisory and do not block merging. CI has no scheduled runs; provider-backed
live tests remain manual.

Before publishing a tag, release verification runs the complete non-live suite
on Python 3.10 through 3.13 and checks the managed OpenCode asset on Linux,
Claude asset on macOS, and Codex asset on Windows. The full seven-case managed
asset matrix remains available by manually dispatching CI. Post-publication
PyPI and public installer checks still run after upload.

Assets include the three wheels, versioned `install.sh`, stable first `install-latest.sh`, `SHA256SUMS`, and `manifest.json`. The shell installer chooses the highest final version and falls back to prereleases only when no final release exists. Use `--prerelease` for an alpha, or `--tag vX.Y.Z` for an exact tag.

The landing build fetches `install-latest.sh` from its pinned M3 GitHub Release,
checks the release version and source commit, verifies the asset against
`manifest.json`, and publishes it at the stable URL. Updating the landing
release pin advances the documentation and installer together.

## Recovery after partial publication

If PyPI accepted only some files or the publish job stopped after upload, dispatch `Release CLI` with operation `recover_tag` and the existing `vX.Y.Z` tag. Recovery downloads the existing draft assets, checks every manifest hash and the checksum file, compares any already published PyPI file byte for byte with the staged wheel, and publishes missing files without rebuilding. A mismatch stops recovery. PyPI can temporarily show a package version while its GitHub release remains a draft.

A manual dispatch with the default `build` operation creates a verification artifact without publishing a release.

## Local metadata check

```sh
uv run --no-project --with packaging python scripts/prepare_release.py 0.2.0 --dry-run
uv run --no-project --with packaging python scripts/prepare_release.py 0.2.0 --check
```

The `--check` form validates a prepared version and current lockfile.

The POSIX shell installer supports macOS and Linux with uv or Python 3.10+.
