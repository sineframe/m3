# Releasing M3

M3 publishes version matched `sf-m3`, `sf-m3-app`, and `sf-m3-cli` wheels to PyPI. The `m3` Python import and `m3` command remain stable public interfaces. The tag controls whether a GitHub Release is final or a prerelease: `v0.2.0` is final and `v0.3.0a1` is an alpha.

## Release from a tag

Prepare a final or prerelease tag from the reviewed default-branch commit and push it:

```sh
git tag -a v0.2.0 MAIN_COMMIT_SHA -m "Release v0.2.0"
git push origin v0.2.0
```

For an alpha, use a PEP 440 prerelease tag such as `v0.3.0a1`. The workflow builds the three matching wheels and installer, stages them in a draft GitHub Release, publishes the wheels to PyPI, and runs fresh install checks before publishing the GitHub Release.

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
