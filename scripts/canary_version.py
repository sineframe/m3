"""Print the version for a canary build.

Canary builds are ``<next>.dev<build number>``, where ``<next>`` is the release
after the current workspace version: ``0.2.0a13`` becomes ``0.2.0a14.devN``
and ``1.2.3`` becomes ``1.2.4.devN``. Dev versions sort before ``<next>``, so
the next real release always replaces a canary.

Typical usage::

    uv run --no-project --with packaging python scripts/canary_version.py --build-number 42
"""

from __future__ import annotations

import argparse
import sys

try:
    from packaging.version import Version
except (
    ImportError
) as exc:  # pragma: no cover - the documented command supplies packaging
    raise SystemExit(
        "packaging is required; run this command through `uv run --with packaging`"
    ) from exc

from build_cli_release import ReleaseBuildError, project_versions


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
    args = parser.parse_args(argv)
    try:
        current = next(iter(project_versions().values()))
        print(canary_version(current, args.build_number))
    except (ReleaseBuildError, ValueError) as exc:
        print(f"canary version failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
