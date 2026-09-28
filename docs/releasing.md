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

## Publish the hosted documentation

The docs source ships in an M3 release tag; the landing repository builds the
site from that exact tag. Pushing an M3 tag starts the full wheel and CLI
release workflow, including PyPI publication. There is no docs-only tag or
Cloudflare credential in M3. The landing site does **not** update just because
an M3 tag exists: someone must update and merge its pinned release commit.

For the first docs rollout, merge the M3 docs changes into `main` and prepare
the next M3 version (for example, `v0.2.13` after `v0.2.12`). From the M3
repository, check that the exact commit to tag contains the docs manifest:

```sh
git fetch origin main --tags
git switch main
git pull --ff-only origin main
git show HEAD:docs/site/navigation.json >/dev/null
python3 scripts/validate_docs_site.py
```

Before pushing the tag, build and inspect the combined site from the sibling
landing checkout. This uses local M3 files only for the pre-release preview:

```sh
cd ../sineframe-landing
M3_DOCS_DIR=../m3 npm run build
npm run preview -- --host 127.0.0.1
```

Open `/` and `/docs/` in that preview, check the docs navigation and links,
then stop the preview. After the usual release checks, return to M3 and tag
the reviewed `main` commit. Replace the example version if a different version
is next:

```sh
cd ../m3
git tag -a v0.2.13 HEAD -m "Release v0.2.13"
git push origin v0.2.13
```

Wait for the `Release CLI` workflow to finish and the GitHub Release to be
published. A failed or draft release is not ready to pin in the landing site.
Then update the landing pull request with the released tag:

```sh
cd ../sineframe-landing
# First rollout only; use a new branch from landing main for later releases.
git switch docs/cloudflare-site
npm run docs:pin -- v0.2.13
npm run build
git add docs-site/m3-release.json
git commit -m "Pin M3 docs to v0.2.13"
git push origin docs/cloudflare-site
```

The pin command records the tag's peeled commit. The landing build fetches the
tag, verifies that it still points to that commit, and renders the docs into
`dist/docs`. Check the Cloudflare Pages preview before merging the landing PR:
`/docs/` and `/docs/sdk/quick-start` must show the docs, and an unknown
`/docs/*` path must show a docs 404 with HTTP status 404. Also check `/` and
the existing landing routes. Merging the landing PR triggers its normal
Cloudflare production deployment; verify `https://m3.sineframe.com/docs/`
afterward. For later M3 releases, repeat the pin, build, preview, and landing
merge steps with the new tag. No Cloudflare token is needed in M3.


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
