"""Print the version for a canary build.

Canary builds are ``<next>.dev<build number>``, where ``<next>`` follows the
latest ``v*`` release tag in the checkout's history: after ``v0.2.30`` a canary
is ``0.2.31.devN``, and after ``v1.0.0a3`` it is ``1.0.0a4.devN``. Dev versions
sort after the release they follow and before ``<next>``, so the next real
release always replaces a canary.

Releases take their version from the pushed tag and never bump the project
metadata, so the tags, not ``pyproject.toml``, are the source of truth. The
metadata version is only used when the repository has no release tags yet.

Typical usage::

    uv run --no-project --with packaging python scripts/canary_version.py --build-number 42
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

try:
    from packaging.version import Version
except (
    ImportError
) as exc:  # pragma: no cover - the documented command supplies packaging
    raise SystemExit(
        "packaging is required; run this command through `uv run --with packaging`"
    ) from exc

from build_cli_release import ReleaseBuildError, project_versions

ROOT = Path(__file__).resolve().parents[1]
# The same grammar install-latest.sh accepts for release tags.
_RELEASE_TAG_RE = re.compile(r"^v(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)$")


def latest_release(tags: list[str]) -> str | None:
    """Return the highest release version among ``tags``, without the ``v``."""

    versions = [
        match.group(1) for tag in tags if (match := _RELEASE_TAG_RE.fullmatch(tag))
    ]
    return max(versions, key=Version, default=None)


def _release_tags() -> list[str]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout

    try:
        if git("rev-parse", "--is-shallow-repository").strip() == "true":
            raise ValueError(
                "the checkout is shallow; fetch full history and tags (fetch-depth: 0)"
            )
        return git("tag", "--list", "v*", "--merged", "HEAD").split()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("could not read release tags with git") from exc


def canary_version(current: str, build_number: int) -> str:
    if build_number < 1:
        raise ValueError("build number must be positive")
    version = Version(current)
    if (
        str(version) != current
        or version.epoch
        or version.post is not None
        or version.dev is not None
        or version.local is not None
    ):
        raise ValueError(f"cannot derive a canary from version {current}")
    release = ".".join(str(part) for part in version.release)
    if version.pre is not None:
        phase, serial = version.pre
        base = f"{release}{phase}{serial + 1}"
    else:
        *head, last = version.release
        base = ".".join(str(part) for part in (*head, last + 1))
    return f"{base}.dev{build_number}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-number", required=True, type=int)
    parser.add_argument(
        "--print-base",
        action="store_true",
        help="print the release the canary follows instead of the canary version",
    )
    args = parser.parse_args(argv)
    try:
        base = latest_release(_release_tags())
        if base is None:
            base = next(iter(project_versions().values()))
        print(base if args.print_base else canary_version(base, args.build_number))
    except (ReleaseBuildError, ValueError) as exc:
        print(f"canary version failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
