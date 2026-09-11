"""Render the versioned standalone MCP Pal installer assets."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = "@MCP_PAL_VERSION@"
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+!-]*$")


class InstallerRenderError(ValueError):
    """A safe, user-facing installer rendering error."""


def render_template(template: Path, version: str) -> str:
    text = template.read_text(encoding="utf-8")
    if text.count(PLACEHOLDER) != 1:
        raise InstallerRenderError(
            f"{template.name} must contain exactly one {PLACEHOLDER} placeholder"
        )
    rendered = text.replace(PLACEHOLDER, version)
    if PLACEHOLDER in rendered:
        raise InstallerRenderError(
            f"{template.name} has an unresolved version placeholder"
        )
    return rendered


def render_installers(version: str, output_dir: str | Path) -> tuple[Path, Path]:
    if not VERSION_PATTERN.fullmatch(version) or any(
        character in version for character in "\r\n'\""
    ):
        raise InstallerRenderError(
            "version is not safe to embed in installer templates"
        )
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    rendered_paths: list[Path] = []
    for source_name, destination_name in (
        ("install.sh.in", "install.sh"),
        ("install.ps1.in", "install.ps1"),
    ):
        rendered = render_template(ROOT / "scripts" / source_name, version)
        destination = output / destination_name
        destination.write_text(rendered, encoding="utf-8", newline="\n")
        rendered_paths.append(destination)
    rendered_paths[0].chmod(0o755)
    return rendered_paths[0], rendered_paths[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        shell, powershell = render_installers(args.version, args.output_dir)
    except (InstallerRenderError, OSError) as exc:
        print(f"installer rendering failed: {exc}", file=sys.stderr)
        return 2
    print(shell)
    print(powershell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
