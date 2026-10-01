#!/usr/bin/env python3
"""Render the exact public Python inventory used by the documentation site."""

from __future__ import annotations

import argparse
import enum
import importlib
import inspect
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "site" / "reference" / "python" / "api.md"
SDK_SOURCE = ROOT / "sdk" / "src"
EXTRA_MODULES = (
    "m3.aggregations",
    "m3.elicitation",
    "m3.judges",
    "m3.managed_input_api",
    "m3.storage",
)


def _one_line(value: str | None, fallback: str | None = None) -> str | None:
    if not value:
        return fallback
    paragraph = value.strip().split("\n\n", 1)[0]
    line = " ".join(part.strip() for part in paragraph.splitlines())
    return line.replace("<", "&lt;").replace(">", "&gt;")


def _inline_code(value: object) -> str:
    """Escape characters Vue would parse before Markdown renders."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _signature(value: Any) -> str:
    try:
        signature = str(inspect.signature(value))
        signature = re.sub(
            r"<([^>]+) (?:object )?at 0x[0-9a-fA-F]+>", r"<\1>", signature
        )

        def sort_frozenset(match: re.Match[str]) -> str:
            values = sorted(re.findall(r"'([^']*)'", match.group(1)))
            return "frozenset({" + ", ".join(repr(item) for item in values) + "})"

        return re.sub(r"frozenset\(\{([^}]*)\}\)", sort_frozenset, signature)
    except (TypeError, ValueError):
        return ""


def _members(value: type[Any]) -> list[str]:
    lines: list[str] = []
    fields = getattr(value, "model_fields", None)
    if isinstance(fields, dict):
        for name, field in fields.items():
            annotation = getattr(field, "annotation", Any)
            default = getattr(field, "default", inspect.Parameter.empty)
            suffix = " (required)"
            if (
                default is not inspect.Parameter.empty
                and repr(default) != "PydanticUndefined"
            ):
                suffix = f" (default: `{_inline_code(repr(default))}`)"
            else:
                factory = getattr(field, "default_factory", None)
                if factory is not None:
                    factory_name = getattr(factory, "__qualname__", None)
                    if not isinstance(factory_name, str):
                        factory_name = type(factory).__qualname__
                    factory_module = getattr(factory, "__module__", None)
                    if isinstance(factory_module, str):
                        factory_name = f"{factory_module}.{factory_name}"
                    suffix = f" (default factory: `{_inline_code(factory_name)}`)"
            description = getattr(field, "description", None)
            line = f"- `{_inline_code(f'{name}: {annotation}')}`{suffix}"
            field_description = _one_line(description)
            lines.append(
                f"{line}: {field_description}" if field_description else f"{line}."
            )
    for name, member in value.__dict__.items():
        if name.startswith("_") or name in {"model_config", "model_fields"}:
            continue
        if isinstance(member, property):
            line = f"- `{_inline_code(name)}` (property)"
            description = _one_line(member.__doc__)
            lines.append(f"{line}: {description}" if description else line)
            continue
        raw = (
            member.__func__
            if isinstance(member, (classmethod, staticmethod))
            else member
        )
        if not (inspect.isfunction(raw) or inspect.ismethoddescriptor(raw)):
            continue
        line = f"- `{_inline_code(name + _signature(raw))}`"
        description = _one_line(raw.__doc__)
        lines.append(f"{line}: {description}" if description else line)
    local_names = set(value.__dict__)
    inherited_properties = {
        name
        for owner in value.__mro__[1:]
        for name, member in owner.__dict__.items()
        if owner.__module__.startswith("m3.")
        and not name.startswith("_")
        and isinstance(member, property)
    }
    for name in sorted(inherited_properties - local_names):
        member = inspect.getattr_static(value, name)
        line = f"- `{_inline_code(name)}` (property)"
        description = _one_line(member.__doc__)
        lines.append(f"{line}: {description}" if description else line)
    return lines


def render() -> str:
    sys.path.insert(0, str(SDK_SOURCE))
    from m3._exports import PUBLIC_EXPORTS

    modules: dict[str, tuple[str, ...]] = dict(PUBLIC_EXPORTS)
    for module_name in EXTRA_MODULES:
        module = importlib.import_module(module_name)
        modules[module_name] = tuple(getattr(module, "__all__", ()))

    output = [
        "---",
        'title: "Python API inventory"',
        'description: "Exact signatures, model fields, and public members for the documented release. The capability pages explain how these objects work together."',
        "---",
        "",
        "# Python API inventory",
        "",
        "The entries below list exact signatures, model fields, and public members for",
        "this release. Use the capability pages to see how the objects work together.",
    ]
    for module_name, names in modules.items():
        module = importlib.import_module(module_name)
        output.extend(("", f"## `{module_name}`"))
        for name in names:
            value = getattr(module, name)
            signature = _signature(value) if callable(value) else ""
            output.extend(
                (
                    "",
                    f"### `{name}`",
                    "",
                    f"`{_inline_code(module_name + '.' + name + signature)}`",
                )
            )
            description = (
                "Installed distribution version."
                if name == "__version__"
                else _one_line(
                    value.__doc__ if inspect.isclass(value) else inspect.getdoc(value)
                )
            )
            if description:
                output.extend(("", description))
            if inspect.isclass(value):
                members = _members(value)
                if isinstance(value, type) and issubclass(value, enum.Enum):
                    members.extend(
                        f"- `{_inline_code(name)}` = `{_inline_code(repr(member.value))}`"
                        for name, member in value.__members__.items()
                    )
                if members:
                    output.extend(("", "Public members:", "", *members))
    return "\n".join(output) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            print(
                f"generated API reference is stale: {OUTPUT.relative_to(ROOT)}",
                file=sys.stderr,
            )
            return 1
        print("Python API reference is current")
        return 0
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"Rendered {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
