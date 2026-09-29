# Releasing M3

CLI releases must contain no Firebase or Supabase SDKs, provider configuration,
project URLs or keys, browser ID/access/refresh tokens, or service credentials.
M3 personal access tokens are the only credentials used for API uploads. The
control plane owns the temporary browser sign-in page and Auth configuration.
Before publishing a CLI release, run a browser sign-in smoke test against the
matching deployed control plane: create developer access, verify OS credential
storage, and confirm that managing CI tokens leaves an existing developer
credential untouched.

Release verification scans every archive member in the SDK, application, and
CLI wheels for Firebase/Supabase code and configuration markers, Google/Firebase
API keys, Supabase key prefixes, PEM private keys (including service-account
JSON), and recognizable provider/browser JWT credentials. No wheel, directory,
filename extension, or metadata member is exempt. Signature verification
and expiry are irrelevant to detecting a bundled credential. Recognizable
JWTs whose JSON exceeds safe inspection limits fail closed. All three
wheels reject direct Firebase/Supabase provider requirements, including extra-
and platform-marked requirements, so SDK/application wheel requirements cannot
silently add them to a CLI installation. This is a conservative artifact
policy, not a resolver for arbitrary third-party dependencies from PyPI.

Firebase JWT detection uses the documented
[ID-token issuer](https://firebase.google.com/docs/auth/admin/verify-id-tokens),
[session-cookie issuer](https://firebase.google.com/docs/auth/admin/manage-cookies),
and [custom-token audience](https://firebase.google.com/docs/auth/admin/create-custom-tokens).
Service-account JSON embeds a sensitive private key; PEM private keys are
rejected even without Firebase-specific filenames or project metadata. Public
keys and unrelated JWTs are allowed. These static signatures do not guarantee
detection of obfuscated or opaque credentials; never bundle live credentials.

The control plane persists hashed one-use CLI grants in Supabase Postgres,
checks expiry and the PKCE challenge during exchange, and consumes the grant
atomically with any developer-token issuance. Expired grants are rejected
immediately; database cleanup is separate from authorization. Before
production CLI login, configure `CLI_AUTH_ENABLED=true` and a
`CLI_GRANT_SIGNING_SECRET` containing at least 32 random bytes encoded as
canonical base64url. Keep the same secret server-side on every control-plane
instance; never put it in M3, browser assets, or source control. See the
[control-plane deployment instructions](https://github.com/sineframe/control-plane/blob/main/README.md#persistence-and-deployment)
for production configuration. Review the hosted Auth redirect allowlist,
asymmetric signing-key configuration, and email confirmation/recovery flows
before enabling CLI login in production.

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

When `scripts/install-latest.sh` changes, copy it byte-for-byte to
`sineframe-landing/public/install.sh` in the landing release pull request. The
landing build publishes that reviewed bootstrap at the stable hosted URL.

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
