#!/usr/bin/env python3
"""Check that hand-typed Codex and Pi version references match the verified versions.

``sdk/src/m3/harness/_verified_versions.py`` is the single source of truth for
the harness versions whose elicitation behaviour M3 has characterized. Workflow
YAML, user-facing docs and examples, standalone fixtures, and comments that
record observed behaviour still spell those versions out. This script fails
when any of them names a different version.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS_MODULE = ROOT / "sdk" / "src" / "m3" / "harness" / "_verified_versions.py"
BUMP_HINT = (
    "Bump procedure: change sdk/src/m3/harness/_verified_versions.py, update the "
    "copies listed above, re-run the real-harness gates, then run "
    "scripts/render_skill_references.py."
)

_VERSION = r"(?P<version>\d+\.\d+\.\d+)"

# Excluded from scanning: the lockfile and generated API pages.
EXCLUDED_FILES = frozenset({"uv.lock"})
EXCLUDED_PREFIXES = ("docs/site/reference/python/api/",)

# Deliberately different versions that appear in the forms below as negative or
# unrelated examples, keyed by (path, version).
NOT_VERIFIED_EXAMPLES = frozenset(
    {
        ("sdk/tests/unit/test_codex_pi_adapters.py", "0.1.0"),
        ("sdk/tests/unit/test_harness_characterize.py", "0.156.2"),
        ("sdk/tests/unit/test_managed_runtime_core.py", "1.2.3"),
    }
)


@dataclass(frozen=True)
class Form:
    """One way a verified version is spelled in text."""

    product: str
    pattern: re.Pattern[str]


def _form(product: str, template: str) -> Form:
    return Form(product, re.compile(template.replace("{version}", _VERSION)))


# Forms checked in every tracked file.
CODEX_FORMS = (
    _form("codex", r"@openai/codex@{version}"),
    _form("codex", r"codex-cli {version}"),
    _form("codex", r"\bCodex (?:CLI |App Server )?`?{version}"),
)
PI_FORMS = (
    _form("pi", r"@earendil-works/pi-coding-agent@{version}"),
    _form("pi", r"\bPi (?:version )?`?{version}"),
)
GLOBAL_FORMS = CODEX_FORMS + PI_FORMS

# Forms that only mean "the verified version" in these files. Each must match at
# least once, so a reformat cannot silently remove a file from the check.
CODEX_PIN_ENTRY = _form("codex", r'"version": "{version}"')
PATH_FORMS: dict[str, tuple[Form, ...]] = {
    ".github/workflows/ci.yml": (*CODEX_FORMS[:2], PI_FORMS[0]),
    ".github/workflows/release-cli.yml": CODEX_FORMS[:2],
    "docs/site/guides/elicitation/managed-input.md": (CODEX_PIN_ENTRY,),
    "skills/testing-with-m3/references/guides-elicitation-managed-input.md": (
        CODEX_PIN_ENTRY,
    ),
    "sdk/examples/docs/elicitation-managed-input/example.json": (CODEX_PIN_ENTRY,),
    "sdk/examples/docs/elicitation-managed-input/test_managed_input.py": (
        CODEX_PIN_ENTRY,
    ),
    "sdk/tests/fixtures/codex_app_server_fixture.py": (
        _form("codex", r"M3_DOCS_FIXTURE_VERSION', '{version}'"),
    ),
    "sdk/tests/fixtures/pi_rpc_fixture.py": (_form("pi", r'print\("{version}"\)'),),
}


def load_verified_versions() -> dict[str, str]:
    spec = importlib.util.spec_from_file_location(
        "m3_verified_versions", VERSIONS_MODULE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {VERSIONS_MODULE}")
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True  # keep a source-tree check from caching bytecode
    spec.loader.exec_module(module)
    return {
        "codex": module.CODEX_VERIFIED_VERSION,
        "pi": module.PI_VERIFIED_VERSION,
    }


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [
        path
        for path in result.stdout.split("\0")
        if path
        and path not in EXCLUDED_FILES
        and not path.startswith(EXCLUDED_PREFIXES)
    ]


def read_text(path: str) -> str | None:
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def scan(
    path: str, text: str, forms: tuple[Form, ...], expected: dict[str, str]
) -> tuple[list[str], set[Form]]:
    """Return mismatch messages for ``forms`` in ``text`` and the forms that hit."""
    errors: list[str] = []
    hit: set[Form] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        for form in forms:
            for match in form.pattern.finditer(line):
                hit.add(form)
                found = match.group("version")
                if found == expected[form.product]:
                    continue
                if (path, found) in NOT_VERIFIED_EXAMPLES:
                    continue
                errors.append(
                    f"{path}:{number}: {match.group(0)!r} names {found}, "
                    f"but the verified {form.product} version is "
                    f"{expected[form.product]}"
                )
    return errors, hit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when a reference is stale or no longer found",
    )
    args = parser.parse_args()

    expected = load_verified_versions()
    files = tracked_files()
    errors = [
        f"{path}: listed in PATH_FORMS but not tracked"
        for path in sorted(set(PATH_FORMS) - set(files))
    ]
    for path in files:
        text = read_text(path)
        if text is None:
            continue
        required = PATH_FORMS.get(path, ())
        forms = GLOBAL_FORMS + tuple(f for f in required if f not in GLOBAL_FORMS)
        found, hit = scan(path, text, forms, expected)
        errors.extend(found)
        errors.extend(
            f"{path}: no reference matching {form.pattern.pattern!r}; update "
            "PATH_FORMS in scripts/check_harness_versions.py if it moved"
            for form in required
            if form not in hit
        )

    if errors:
        print("\n".join(errors))
        print(BUMP_HINT)
        return 1 if args.check else 0
    print(
        "Harness version references match "
        f"Codex {expected['codex']} and Pi {expected['pi']}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
