"""Verify already-published files match staged release bytes exactly."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> int:
    paths = [Path(value) for value in sys.argv[1:]]
    if not paths:
        raise SystemExit("usage: pypi_preflight.py WHEEL...")
    for path in paths:
        # Package names may contain dashes; derive normalized name/version from wheel grammar.
        from packaging.utils import parse_wheel_filename

        name, version, _build, _tags = parse_wheel_filename(path.name)
        url = f"https://pypi.org/pypi/{name}/{version}/json"
        try:
            with urlopen(url, timeout=30) as response:
                release = json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise SystemExit(
                f"could not inspect PyPI release {name} {version}"
            ) from None
        except (OSError, URLError, json.JSONDecodeError):
            raise SystemExit(
                f"could not inspect PyPI release {name} {version}"
            ) from None
        record = next(
            (
                item
                for item in release.get("urls", [])
                if item.get("filename") == path.name
            ),
            None,
        )
        if record is None:
            continue
        try:
            with urlopen(record["url"], timeout=60) as response:
                published = hashlib.sha256(response.read()).hexdigest()
            staged = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, URLError, KeyError):
            raise SystemExit(
                f"could not verify existing PyPI file {path.name}"
            ) from None
        if staged != published:
            raise SystemExit(
                f"existing PyPI file differs from staged bytes: {path.name}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
