# Releasing M3

M3 releases contain three version-matched wheels—`m3`,
`m3-app`, and `m3-cli`—plus `SHA256SUMS` and the rendered macOS/Linux
and Windows installers. PyPI publication is currently deferred; GitHub Releases
is the supported distribution channel.

## Prerequisite

The production UI comes from the private `rishhavv/mcppal-ui` repository at the
commit pinned in [`cli/UI_REF`](../cli/UI_REF). Configure the
`MCPPAL_UI_TOKEN` Actions secret with read-only access to that repository. CI
and the release workflow both verify and build that exact commit; the token is
not included in release artifacts.

## Release from a tag

The pushed tag supplies the release version. Tag the exact default-branch
commit and push it:

```bash
git tag -a vX.Y.Z MAIN_COMMIT_SHA -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

The [release workflow](../.github/workflows/release-cli.yml) synchronizes all
three package versions and the CLI's exact internal dependency pins in a
disposable checkout. It regenerates the lockfile, builds the pinned UI, runs
the release gates, and publishes the artifacts without committing generated
version changes to the repository.

## Manual preparation check

Maintainers can validate source metadata and the lockfile before tagging:

```bash
just prepare-release X.Y.Z
uv run --no-project --with packaging python scripts/prepare_release.py X.Y.Z --dry-run
uv run --no-project --with packaging python scripts/prepare_release.py X.Y.Z --check
```

These commands do not create a tag or publish a release. A manual workflow
dispatch builds and verifies the same payload without publishing it.
