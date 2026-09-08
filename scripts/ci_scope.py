"""Select compatibility suites from the paths changed in a git range."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import subprocess
from collections.abc import Iterable


@dataclass(frozen=True, slots=True)
class Scope:
    sdk: bool = False
    app: bool = False
    cli: bool = False

    @property
    def any(self) -> bool:
        return self.sdk or self.app or self.cli

    @classmethod
    def all(cls) -> "Scope":
        return cls(sdk=True, app=True, cli=True)

    def as_outputs(self) -> dict[str, str]:
        return {
            "sdk": str(self.sdk).lower(),
            "app": str(self.app).lower(),
            "cli": str(self.cli).lower(),
            "any": str(self.any).lower(),
        }


_DOC_SUFFIXES = (".md", ".mdx", ".rst")
_DOC_DIRECTORIES = ("docs/", "documentation/", "plans/", ".planning/")


def _is_documentation(path: str) -> bool:
    normalized = path.removeprefix("./")
    return normalized.startswith(_DOC_DIRECTORIES) or normalized.endswith(_DOC_SUFFIXES)


def scope_for_paths(paths: Iterable[str]) -> Scope:
    """Return the suites affected by repository-relative changed paths.

    The default is deliberately conservative: an unrecognized non-document
    path runs every suite rather than silently reducing compatibility coverage.
    """

    result = Scope()
    for raw_path in paths:
        path = raw_path.strip().replace("\\", "/").removeprefix("./")
        if not path or _is_documentation(path):
            continue
        if (
            path == "uv.lock"
            or path == "pyproject.toml"
            or path.startswith(".github/")
            or "/pyproject.toml" in path
            or path.startswith("scripts/")
        ):
            return Scope.all()
        if path.startswith("sdk/"):
            result = Scope(sdk=True, app=True, cli=True)
        elif path.startswith("app/"):
            result = Scope(sdk=result.sdk, app=True, cli=True)
        elif path.startswith("cli/"):
            result = Scope(sdk=result.sdk, app=result.app, cli=True)
        else:
            return Scope.all()
    return result


def changed_paths(base: str, head: str) -> tuple[str, ...] | None:
    """Read changed paths, returning ``None`` when the git range is invalid."""

    try:
        completed = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "--diff-filter=ACMRTUXBD",
                base,
                head,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return tuple(line for line in completed.stdout.splitlines() if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument(
        "--all", action="store_true", help="run every compatibility suite"
    )
    args = parser.parse_args()
    paths = None if args.all or not args.base or not args.head else changed_paths(args.base, args.head)
    scope = Scope.all() if args.all or paths is None else scope_for_paths(paths)
    for name, value in scope.as_outputs().items():
        print(f"{name}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
