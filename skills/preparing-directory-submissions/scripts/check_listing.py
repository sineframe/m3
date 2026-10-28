#!/usr/bin/env python3
"""Check listing text and assets against the directories' published limits.

OpenAI: reads a Codex manifest (.codex-plugin/plugin.json) or an Agent
Plugins manifest (plugin.json with extensions.com.openai). Claude: checks the
portal fields passed on the command line.

Prints one line per check: PASS, FAIL (a published limit is broken), or JUDGE
(text you must read and decide on, such as pricing or a generic name).
Exits 1 if any line is FAIL, 2 if there was nothing to check, else 0.

Usage:
  check_listing.py [MANIFEST]
  check_listing.py --claude-name TEXT --claude-one-liner TEXT --claude-description TEXT
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROMO = re.compile(
    r"(?<!\w)(free|trial|discount|\d+\s?% off|save \d+|pricing|price|subscribe|subscription|"
    r"upgrade|best|#1|number one|top[- ]rated|official|leading|cheapest|fastest|"
    r"better than|unlike|compared (?:to|with)|vs\.?)(?!\w)",
    re.IGNORECASE,
)
SUFFIX = re.compile(r"\b(MCP Server|MCP|Plugin)\s*$", re.IGNORECASE)


FAILED: list[str] = []


def line(status: str, check: str, detail: str) -> None:
    if status == "FAIL":
        FAILED.append(check)
    print(f"{status:6} {check}: {detail}")


def length(check: str, text: object, limit: int, required: bool = True) -> None:
    if not isinstance(text, str) or not text.strip():
        if required:
            line("FAIL", check, "missing")
        return
    count = len(text)
    line(
        "PASS" if count <= limit else "FAIL",
        check,
        f"{count} characters (limit {limit})",
    )


def promo(check: str, text: object) -> None:
    if isinstance(text, str):
        for match in PROMO.finditer(text):
            start = max(0, match.start() - 30)
            line(
                "JUDGE",
                check,
                f"possible pricing, promotion, ranking or comparison: “…{text[start : match.end() + 30]}…”",
            )


def image_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X":
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(
                data[27:30], "little"
            )
        if chunk == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return None
    sizes = svg_sizes(data)
    if sizes:
        # OpenAI accepts square numeric dimensions or a square viewBox, so
        # prefer whichever of the two qualifies.
        good = [(w, h) for w, h in sizes if w == h and w >= 48]
        return (good or sizes)[0]
    return None


def svg_sizes(data: bytes) -> list[tuple[int, int]]:
    """The numeric width/height and the viewBox size of an SVG, as found."""
    text = data[:4096].decode("utf-8", "replace")
    tag = re.search(r"<svg\b[^>]*>", text)
    if tag is None:
        return []
    attributes = tag.group(0)
    sizes = []
    w = re.search(r"""(?<![\w-])width=["']([\d.]+)(?:px)?["']""", attributes)
    h = re.search(r"""(?<![\w-])height=["']([\d.]+)(?:px)?["']""", attributes)
    if w and h:
        sizes.append((int(float(w.group(1))), int(float(h.group(1)))))
    box = re.search(
        r"""viewBox=["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)""",
        attributes,
    )
    if box:
        sizes.append((int(float(box.group(1))), int(float(box.group(2)))))
    return sizes


def icon(check: str, root: Path, value: object) -> None:
    if not isinstance(value, str) or not value:
        line(
            "FAIL", check, "missing; a square icon of at least 48 by 48 px is required"
        )
        return
    path = (root / value).resolve()
    if not path.is_file():
        line("FAIL", check, f"{value} does not exist")
        return
    problems = []
    if path.stat().st_size > 5 * 1024 * 1024:
        problems.append("larger than 5 MiB")
    size = image_size(path)
    if size is None:
        if problems:
            line("FAIL", check, f"{value} is {', '.join(problems)}")
        line(
            "JUDGE",
            check,
            f"{value}: could not read the dimensions; check it is square and at least 48 px",
        )
        return
    w, h = size
    if w != h:
        problems.append("not square")
    if min(w, h) < 48:
        problems.append("smaller than 48 px")
    if max(w, h) > 4096 and not svg_sizes(path.read_bytes()):
        problems.append("larger than 4096 px")
    line(
        "FAIL" if problems else "PASS",
        check,
        f"{value} is {w}x{h}" + (f" ({', '.join(problems)})" if problems else ""),
    )


def url(check: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        line("FAIL", check, "missing; required for MCP review")
        return
    parts = urlsplit(value)
    if parts.scheme != "https":
        line("FAIL", check, f"{value} is not https")
    elif parts.username or parts.password:
        line("FAIL", check, "URL embeds credentials")
    elif len(value) > 1024:
        line("FAIL", check, f"{len(value)} characters (limit 1024)")
    else:
        line("PASS", check, value)


def find_manifest(start: Path) -> Path | None:
    for candidate in (start / "plugin.json", start / ".codex-plugin" / "plugin.json"):
        if candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if candidate.name == "plugin.json" and candidate.parent == start:
                if "com.openai" not in data.get("extensions", {}):
                    continue
            return candidate
    return None


def check_openai(manifest: Path) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    extension = data.get("extensions", {}).get("com.openai", {})
    interface = extension.get("interface") or data.get("interface") or {}
    root = (
        manifest.parent.parent
        if manifest.parent.name == ".codex-plugin"
        else manifest.parent
    )
    codex = manifest.parent.name == ".codex-plugin"
    print(
        f"OpenAI listing from {manifest} "
        f"({'Codex' if codex else 'Agent Plugins'} format)"
    )

    name = interface.get("displayName")
    length("displayName", name, 30)
    if isinstance(name, str):
        if SUFFIX.search(name):
            line(
                "FAIL",
                "displayName",
                f"“{name}” appends “{SUFFIX.search(name).group(1)}” to the product name",
            )
        if len(name.split()) == 1:
            line(
                "JUDGE",
                "displayName",
                f"“{name}” is one word; is it a generic dictionary term not tied to the brand?",
            )
    length("shortDescription", interface.get("shortDescription"), 30)
    length("longDescription", interface.get("longDescription"), 4000)
    for field in ("shortDescription", "longDescription"):
        promo(field, interface.get(field))
    for field in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
        url(field, interface.get(field))
    length("developerName", interface.get("developerName"), 80)
    category = interface.get("category")
    if isinstance(category, str) and category.strip():
        line("PASS", "category", category)
    else:
        line(
            "FAIL",
            "category",
            "missing; required (a category title from the dashboard)",
        )
    capabilities = interface.get("capabilities")
    if isinstance(capabilities, list):
        if len(capabilities) > 20:
            line("FAIL", "capabilities", f"{len(capabilities)} labels (at most 20)")
        for index, label in enumerate(capabilities, 1):
            length(f"capabilities[{index}]", label, 120)
        if not capabilities:
            line("PASS", "capabilities", "[] (no labels)")
    elif codex:
        line(
            "FAIL",
            "capabilities",
            "missing; required in the Codex format (use [] if none)",
        )

    # Codex requires both icons; a portable package may leave the primary
    # icon to the dashboard, which needs one before submission.
    if interface.get("logo") or codex:
        icon("logo", root, interface.get("logo"))
    else:
        line(
            "JUDGE",
            "logo",
            "missing; the dashboard requires a primary icon before submission: add "
            "logo here or provide the icon in the dashboard",
        )
    if interface.get("composerIcon") or codex:
        icon("composerIcon", root, interface.get("composerIcon"))

    prompts = interface.get("defaultPrompt")
    if isinstance(prompts, str):
        prompts = [prompts]
    if isinstance(prompts, list):
        if len(prompts) > 3:
            line("FAIL", "defaultPrompt", f"{len(prompts)} prompts (at most 3)")
        for index, prompt in enumerate(prompts, 1):
            length(f"defaultPrompt[{index}]", prompt, 128)

    cases = (extension.get("review") or {}).get("test_cases")
    if isinstance(cases, dict):
        positive = len(cases.get("positive") or [])
        negative = len(cases.get("negative") or [])
        line(
            "PASS" if positive == 5 else "FAIL",
            "review.test_cases.positive",
            f"{positive} cases (exactly 5 for initial review)",
        )
        line(
            "PASS" if negative == 3 else "FAIL",
            "review.test_cases.negative",
            f"{negative} cases (exactly 3 for initial review)",
        )
    else:
        line(
            "JUDGE",
            "review.test_cases",
            "not in the manifest; enter 5 positive and 3 negative cases in the portal",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", nargs="?")
    parser.add_argument("--claude-name")
    parser.add_argument("--claude-one-liner")
    parser.add_argument("--claude-description")
    args = parser.parse_args()

    claude = (args.claude_name, args.claude_one_liner, args.claude_description)
    if any(value is not None for value in claude):
        print("Claude listing (portal fields)")
        length("server name", args.claude_name, 100)
        length("one-liner", args.claude_one_liner, 200)
        length("description", args.claude_description, 2000)
        for field, value in zip(("one-liner", "description"), claude[1:], strict=True):
            promo(field, value)
        if args.manifest is None:
            return 1 if FAILED else 0

    manifest = Path(args.manifest) if args.manifest else find_manifest(Path.cwd())
    if manifest is None or not manifest.is_file():
        print(
            "No OpenAI manifest found (.codex-plugin/plugin.json, or plugin.json with extensions.com.openai)"
        )
        return 2
    check_openai(manifest)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
