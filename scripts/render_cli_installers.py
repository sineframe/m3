"""Render the versioned standalone M3 installer assets."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PLACEHOLDER = "@M3_VERSION@"
RELEASE_TAG_PLACEHOLDER = "@M3_RELEASE_TAG@"
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+!-]*$")
RELEASE_TAG_PATTERN = re.compile(
    r"^(?:v[0-9A-Za-z.+!-]+|canary-(?:main|pr-[1-9][0-9]*))$"
)


class InstallerRenderError(ValueError):
    """A safe, user-facing installer rendering error."""


def render_template(template: Path, values: dict[str, str]) -> str:
    text = template.read_text(encoding="utf-8")
    for placeholder in values:
        if text.count(placeholder) != 1:
            raise InstallerRenderError(
                f"{template.name} must contain exactly one {placeholder} placeholder"
            )
    rendered = text
    for placeholder, value in values.items():
        rendered = rendered.replace(placeholder, value)
    if any(placeholder in rendered for placeholder in values):
        raise InstallerRenderError(f"{template.name} has an unresolved placeholder")
    return rendered


def render_installer(
    version: str, output_dir: str | Path, release_tag: str | None = None
) -> Path:
    if not VERSION_PATTERN.fullmatch(version) or any(
        character in version for character in "\r\n'\""
    ):
        raise InstallerRenderError(
            "version is not safe to embed in installer templates"
        )
    release_tag = f"v{version}" if release_tag is None else release_tag
    if not RELEASE_TAG_PATTERN.fullmatch(release_tag):
        raise InstallerRenderError(
            "release tag must be v<version>, canary-main, or canary-pr-<number>"
        )
    values = {
        VERSION_PLACEHOLDER: version,
        RELEASE_TAG_PLACEHOLDER: release_tag,
    }
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []
    for source_name, destination_name in (("install.sh.in", "install.sh"),):
        rendered = render_template(ROOT / "scripts" / source_name, values)
        destination = output / destination_name
        destination.write_text(rendered, encoding="utf-8", newline="\n")
        rendered_paths.append(destination)
    rendered_paths[0].chmod(0o755)
    return rendered_paths[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--release-tag", help="GitHub release tag to download from (default v<version>)"
    )
    args = parser.parse_args(argv)
    try:
        shell = render_installer(args.version, args.output_dir, args.release_tag)
    except (InstallerRenderError, OSError) as exc:
        print(f"installer rendering failed: {exc}", file=sys.stderr)
        return 2
    print(shell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
