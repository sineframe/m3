# Releasing M3

M3 publishes version matched `sf-m3`, `sf-m3-app`, and `sf-m3-cli` wheels to PyPI. The `m3` Python import and `m3` command remain stable public interfaces. The tag controls whether a GitHub Release is final or a prerelease: `v0.2.0` is final and `v0.3.0a1` is an alpha.

## One-time setup

Before the first tag, configure three PyPI Trusted Publishers for owner `sineframe`, repository `m3`, workflow `release-cli.yml`, and the GitHub environment `pypi`, for projects `sf-m3`, `sf-m3-app`, and `sf-m3-cli`. Use PyPI's pending publisher setup for names that have not yet been created. The publish job requests `id-token: write` and uses `uv publish`; it does not store a PyPI API token.

Configure the `MCPPAL_UI_TOKEN` Actions secret with read-only access to private `rishhavv/m3-ui`. Release and private UI CI builds check out the exact commit recorded in [`cli/UI_REF`](../cli/UI_REF). Public fixture UI packaging checks do not need this secret.

## Release from a tag

Prepare a final or prerelease tag from the reviewed default-branch commit and push it:

```sh
git tag -a v0.2.0 MAIN_COMMIT_SHA -m "Release v0.2.0"
git push origin v0.2.0
```

For an alpha, use a PEP 440 prerelease tag such as `v0.3.0a1`. The workflow derives versions, validates the tag, synchronizes all three projects and exact internal pins in a disposable checkout, and builds the pinned UI and wheels. It records wheel and installer hashes plus source and UI commits in a manifest, then stages those exact files in a draft GitHub Release before publishing wheels to PyPI. The draft becomes public after fresh PyPI installs pass.

Assets include the three wheels, versioned `install.sh`, stable first `install-latest.sh`, `SHA256SUMS`, and `manifest.json`. The public bootstrap chooses the highest final version and falls back to prereleases only when no final release exists. Use `--prerelease` for an alpha, or `--tag vX.Y.Z` for an exact tag.

## Recovery after partial publication

If PyPI accepted only some files or the publish job stopped after upload, dispatch `Release CLI` with operation `recover_tag` and the existing `vX.Y.Z` tag. Recovery downloads the existing draft assets, checks every manifest hash and the checksum file, compares any already published PyPI file byte for byte with the staged wheel, and publishes missing files without rebuilding. A mismatch stops recovery. PyPI can temporarily show a package version while its GitHub release remains a draft.

A manual dispatch with the default `build` operation only creates a verification artifact; it does not publish. It still needs the private UI token because it builds the pinned production UI.

## Local metadata check

```sh
uv run --no-project --with packaging python scripts/prepare_release.py 0.2.0 --dry-run
uv run --no-project --with packaging python scripts/prepare_release.py 0.2.0 --check
```

The `--check` form validates a prepared version and current lockfile. Neither command creates a tag or publishes a release.

The POSIX shell installer supports macOS and Linux with uv or Python 3.10+.
