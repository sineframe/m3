#!/usr/bin/env python3
"""Render the exact public Python inventory used by the documentation site."""

from __future__ import annotations

import argparse
import collections.abc
import enum
import importlib
import inspect
import re
import sys
import types
import typing
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "site" / "reference" / "python" / "api.md"
MODULE_OUTPUT_DIR = OUTPUT.parent / "api"
SDK_SOURCE = ROOT / "sdk" / "src"
EXTRA_MODULES = (
    "m3.aggregations",
    "m3.elicitation",
    "m3.judges",
    "m3.managed_input_api",
    "m3.storage",
)

_CONSTRAINT_NAMES = (
    "min_length",
    "max_length",
    "ge",
    "gt",
    "le",
    "lt",
    "multiple_of",
    "pattern",
    "max_digits",
    "decimal_places",
    "strict",
    "discriminator",
    "union_mode",
    "allow_inf_nan",
)


def _code_span(value: object, *, in_table: bool = False) -> str:
    """Render a Markdown code span without escaping its literal contents."""
    text = str(value)
    runs = re.findall(r"`+", text)
    delimiter = "`" * (max((len(run) for run in runs), default=0) + 1)
    if (
        text.startswith("`")
        or text.endswith("`")
        or (text.startswith(" ") and text.endswith(" "))
    ):
        text = f" {text} "
    # GFM table parsing recognizes escaped pipes inside code spans.
    if in_table:
        text = text.replace("|", r"\|")
    return f"{delimiter}{text}{delimiter}"


def _one_line(value: str | None, fallback: str | None = None) -> str | None:
    if not value:
        return fallback
    paragraph = value.strip().split("\n\n", 1)[0]
    line = " ".join(part.strip() for part in paragraph.splitlines())
    return line.replace("<", "&lt;").replace(">", "&gt;")


def _description(value: Any, name: str) -> str | None:
    if name == "__version__":
        return "Installed distribution version."
    if inspect.isclass(value):
        doc = value.__doc__
        if not doc:
            return None
        first_line = doc.strip().splitlines()[0].strip()
        # Dataclasses and Pydantic models often publish their generated
        # constructor signature as __doc__. The formatted signature and model
        # field table above already carry that information.
        if first_line.startswith(f"{value.__name__}("):
            return None
        if value.__module__ == "builtins":
            return None
        return _one_line(doc)
    if inspect.isfunction(value) or inspect.isbuiltin(value) or inspect.ismethod(value):
        return _one_line(inspect.getdoc(value))
    return None


def _module_slug(module_name: str) -> str:
    return module_name.removeprefix("m3.").replace("_", "-").replace(".", "-")


def _heading_slug(value: str) -> str:
    title = unicodedata.normalize("NFKD", value.replace("_", " "))
    title = title.encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9 -]", "", title).strip().replace(" ", "-")
    return re.sub(r"-+", "-", slug)


def module_inventory() -> list[tuple[str, str, str, tuple[str, ...]]]:
    """Return stable module metadata shared with the documentation navigator."""
    sys.path.insert(0, str(SDK_SOURCE))
    from m3._exports import PUBLIC_EXPORTS

    modules: dict[str, tuple[str, ...]] = dict(PUBLIC_EXPORTS)
    for module_name in EXTRA_MODULES:
        module = importlib.import_module(module_name)
        modules[module_name] = tuple(getattr(module, "__all__", ()))
    return [
        (module_name, _module_slug(module_name), module_name, names)
        for module_name, names in modules.items()
    ]


def _type_name(annotation: Any) -> str:
    """Return a compact, readable spelling of a Python type annotation."""
    if annotation is inspect.Parameter.empty:
        return "Any"
    if isinstance(annotation, str):
        if (
            len(annotation) >= 2
            and annotation[0] == annotation[-1]
            and annotation[0] in "\"'"
        ):
            annotation = annotation[1:-1]
        return annotation
    if annotation is Any or annotation is typing.Any:
        return "Any"
    if isinstance(annotation, typing.TypeVar):
        return annotation.__name__
    if isinstance(annotation, type):
        if annotation is type(None):
            return "None"
        generic = getattr(annotation, "__pydantic_generic_metadata__", None)
        if isinstance(generic, dict) and generic.get("args"):
            base = generic.get("origin") or annotation
            base_name = f"{base.__module__}.{base.__qualname__}"
            return (
                f"{base_name}[{', '.join(_type_name(arg) for arg in generic['args'])}]"
            )
        if annotation.__module__ == "builtins":
            return annotation.__qualname__
        return f"{annotation.__module__}.{annotation.__qualname__}"

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Annotated:
        return _type_name(args[0]) if args else "Any"
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_type_name(arg) for arg in args)
    if origin is typing.Literal:
        return "Literal[" + ", ".join(repr(arg) for arg in args) + "]"
    if origin is collections.abc.Callable and len(args) == 2:
        callable_args, result = args
        if callable_args is Ellipsis:
            rendered_args = "..."
        else:
            rendered_args = (
                "[" + ", ".join(_type_name(arg) for arg in callable_args) + "]"
            )
        return f"Callable[{rendered_args}, {_type_name(result)}]"
    if origin is not None:
        if origin is type:
            base = "type"
        elif isinstance(origin, type) and origin.__module__ == "builtins":
            base = origin.__qualname__
        elif isinstance(origin, type):
            base = f"{origin.__module__}.{origin.__qualname__}"
        else:
            base = str(origin).replace("typing.", "")
        return f"{base}[{', '.join(_type_name(arg) for arg in args)}]"

    if annotation is Ellipsis:
        return "..."

    text = str(annotation)
    text = text.replace("typing.", "")
    text = re.sub(r"<class '([^']+)'>", r"\1", text)
    text = re.sub(r"<enum '([^']+)'>", r"\1", text)
    return text


def _signature_annotation(annotation: Any) -> str:
    """Retain Annotated metadata in callable signatures without repr internals."""
    if typing.get_origin(annotation) is typing.Annotated:
        args = typing.get_args(annotation)
        if not args:
            return "Any"
        metadata_text: list[str] = []
        for metadata in args[1:]:
            values = _metadata_constraints(metadata)
            class_name = type(metadata).__name__
            if values and all("=" in value for value in values):
                metadata_text.append(f"{class_name}({', '.join(values)})")
            else:
                metadata_text.append(_value_repr(metadata))
        return (
            f"Annotated[{_signature_annotation(args[0])}, {', '.join(metadata_text)}]"
        )
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(
            _signature_annotation(arg) for arg in typing.get_args(annotation)
        )
    if origin is typing.Literal:
        return _type_name(annotation)
    args = typing.get_args(annotation)
    if origin is not None and args:
        if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
            return f"tuple[{_signature_annotation(args[0])}, ...]"
        base = _type_name(origin)
        if origin is collections.abc.Callable and len(args) == 2:
            return _type_name(annotation)
        return f"{base}[{', '.join(_signature_annotation(arg) for arg in args)}]"
    return _type_name(annotation)


def _value_repr(value: Any) -> str:
    if isinstance(value, enum.Enum):
        return f"{type(value).__qualname__}.{value.name} ({value.value!r})"
    text = repr(value)
    return re.sub(r"<([^>]+) object at 0x[0-9a-fA-F]+>", r"<\1>", text)


def _field_default(field: Any) -> tuple[str, str]:
    required = getattr(field, "is_required", None)
    if callable(required) and required():
        return "Yes", "—"
    factory = getattr(field, "default_factory", None)
    if factory is not None:
        factory_name = getattr(factory, "__qualname__", type(factory).__qualname__)
        factory_module = getattr(factory, "__module__", None)
        if isinstance(factory_module, str):
            factory_name = f"{factory_module}.{factory_name}"
        return "No", f"factory {factory_name}()"
    default = getattr(field, "default", inspect.Parameter.empty)
    if (
        default is inspect.Parameter.empty
        or type(default).__name__ == "PydanticUndefinedType"
    ):
        return "Yes", "—"
    return "No", _value_repr(default)


def _signature_default(field: Any) -> str:
    factory = getattr(field, "default_factory", None)
    if factory is not None:
        return "..."
    default = getattr(field, "default", inspect.Parameter.empty)
    if isinstance(default, enum.Enum):
        return f"{type(default).__qualname__}.{default.name}"
    return _signature_value_repr(default)


def _metadata_constraints(metadata: Any) -> list[str]:
    values: list[str] = []
    for key in _CONSTRAINT_NAMES:
        value = getattr(metadata, key, None)
        if value is not None:
            values.append(f"{key}={value!r}")
    # Preserve Pydantic metadata we do not recognize rather than silently
    # dropping a contract detail.
    if not values:
        name = type(metadata).__name__
        if name not in {"NoneType", "FieldInfo"}:
            values.append(_value_repr(metadata))
    return values


def _field_constraints(field: Any) -> str:
    metadata: list[tuple[str, Any]] = [
        ("", item) for item in (getattr(field, "metadata", ()) or ())
    ]

    def collect(annotation: Any, path: str = "") -> None:
        args = typing.get_args(annotation)
        if not args and isinstance(annotation, type):
            generic = getattr(annotation, "__pydantic_generic_metadata__", None)
            if isinstance(generic, dict):
                args = tuple(generic.get("args", ()))
        if typing.get_origin(annotation) is typing.Annotated:
            if args:
                collect(args[0], path)
            metadata.extend((path, item) for item in args[1:])
        elif typing.get_origin(annotation) in (typing.Union, types.UnionType):
            for index, arg in enumerate(args):
                if arg is not type(None):
                    collect(arg, f"variant {index + 1}")
        else:
            for index, arg in enumerate(args):
                if arg is not Ellipsis:
                    collect(
                        arg, "items" if len(args) == 1 else f"type argument {index + 1}"
                    )

    collect(getattr(field, "annotation", Any))
    field_discriminator = getattr(field, "discriminator", None)
    if field_discriminator is not None:
        metadata.append(
            ("", type("FieldConstraint", (), {"discriminator": field_discriminator})())
        )
    flattened: list[str] = []
    for path, item in metadata:
        for value in _metadata_constraints(item):
            if path:
                value = f"{path}: {value}"
            if value not in flattened:
                flattened.append(value)
    return ", ".join(flattened) if flattened else "—"


def _signature(value: Any) -> inspect.Signature | None:
    try:
        return inspect.signature(value)
    except (TypeError, ValueError):
        return None


def _parameter_lines(
    signature: inspect.Signature, model_fields: Any = None
) -> list[str]:
    parameters = list(signature.parameters.values())
    lines: list[str] = []
    has_var_positional = any(
        p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters
    )
    positional_only = [
        i
        for i, p in enumerate(parameters)
        if p.kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    for index, parameter in enumerate(parameters):
        if positional_only and index == positional_only[-1] + 1:
            lines.append("    /,")
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY and not has_var_positional:
            if not any(line.strip() in {"*", "*,"} for line in lines):
                lines.append("    *,")
        field = (
            model_fields.get(parameter.name) if isinstance(model_fields, dict) else None
        )
        if field is not None:
            annotation = _type_name(getattr(field, "annotation", Any))
            required, _ = _field_default(field)
            rendered = f"{parameter.name}: {annotation}"
            if required == "No":
                rendered += f" = {_signature_default(field)}"
        else:
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                rendered = "*" + parameter.name
            elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
                rendered = "**" + parameter.name
            else:
                rendered = parameter.name
            if parameter.annotation is not inspect.Parameter.empty:
                rendered += f": {_signature_annotation(parameter.annotation)}"
            if parameter.default is not inspect.Parameter.empty:
                rendered += f" = {_signature_value_repr(parameter.default)}"
        lines.append(f"    {rendered},")
    if positional_only and positional_only[-1] == len(parameters) - 1:
        lines.append("    /,")
    return lines


def _signature_value_repr(value: Any) -> str:
    if isinstance(value, enum.Enum):
        return f"{type(value).__qualname__}.{value.name}"
    if inspect.isfunction(value) or inspect.isbuiltin(value):
        module = getattr(value, "__module__", None)
        qualname = getattr(value, "__qualname__", value.__name__)
        return f"{module}.{qualname}" if module else qualname
    if isinstance(value, frozenset):
        if not value:
            return "frozenset()"
        return "frozenset({" + ", ".join(sorted(repr(item) for item in value)) + "})"
    if isinstance(value, set):
        if not value:
            return "set()"
        return "{" + ", ".join(sorted(repr(item) for item in value)) + "}"
    original = repr(value)
    if original == "<factory>":
        return "..."
    text = _value_repr(value)
    if " object at " in original:
        return "..."
    return text


def _signature_block(name: str, value: Any, model_fields: Any = None) -> str:
    signature = _signature(value)
    if signature is None:
        return f"```python\n{name}\n```"
    lines = [f"{name}(", *_parameter_lines(signature, model_fields), ")"]
    if signature.return_annotation is not inspect.Signature.empty:
        lines[-1] += f" -> {_type_name(signature.return_annotation)}"
    return "```python\n" + "\n".join(lines) + "\n```"


def _members(value: type[Any]) -> list[str]:
    lines: list[str] = []
    fields = getattr(value, "model_fields", None)
    if isinstance(fields, dict) and fields:
        lines.extend(
            (
                "Model fields:",
                "",
                "| Field | Type | Required | Default | Constraints | Description |",
                "| --- | --- | --- | --- | --- | --- |",
            )
        )
        for name, field in fields.items():
            annotation = _type_name(getattr(field, "annotation", Any))
            required, default = _field_default(field)
            description = _one_line(getattr(field, "description", None)) or "—"
            constraints = _field_constraints(field)
            cells = (
                _code_span(name, in_table=True),
                _code_span(annotation, in_table=True),
                required,
                _code_span(default, in_table=True) if default != "—" else default,
                _code_span(constraints, in_table=True) if constraints != "—" else "—",
                description.replace("|", "&#124;"),
            )
            lines.append("| " + " | ".join(cells) + " |")
    for name, member in value.__dict__.items():
        if name.startswith("_") or name in {"model_config", "model_fields"}:
            continue
        if isinstance(member, property):
            line = f"- {_code_span(name)} (property)"
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
        lines.extend(("", _signature_block(name, raw)))
        description = _one_line(raw.__doc__)
        if description:
            lines.append(description)
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
        line = f"- {_code_span(name)} (property)"
        description = _one_line(member.__doc__)
        lines.append(f"{line}: {description}" if description else line)
    return lines


def render_module(module_name: str, names: tuple[str, ...]) -> str:
    module = importlib.import_module(module_name)
    output = [
        "---",
        f'title: "{module_name}"',
        f'description: "Public Python API reference for {module_name}."',
        "---",
        "",
        f"# `{module_name}`",
        "",
        "Signatures use `...` for factory-backed or opaque defaults. Model field",
        "tables show required status, defaults, constraints, and descriptions.",
    ]
    for name in names:
        value = getattr(module, name)
        fields = (
            getattr(value, "model_fields", None) if inspect.isclass(value) else None
        )
        output.extend(
            (
                "",
                f"## `{name}`",
                "",
                _signature_block(f"{module_name}.{name}", value, fields)
                if callable(value)
                else _code_span(f"{module_name}.{name}"),
            )
        )
        description = _description(value, name)
        if description:
            output.extend(("", description))
        if inspect.isclass(value):
            members = _members(value)
            if isinstance(value, type) and issubclass(value, enum.Enum):
                members.extend(
                    f"- {_code_span(member_name)} = {_code_span(_value_repr(member.value))}"
                    for member_name, member in value.__members__.items()
                )
            if members:
                output.extend(("", *members))
    return "\n".join(output) + "\n"


def render() -> str:
    modules = module_inventory()
    output = [
        "---",
        'title: "Python API inventory"',
        'description: "Callable signatures, model fields, and public members for the documented release. The capability pages explain how these objects work together."',
        "---",
        "",
        "# Python API inventory",
        "",
        "The entries below link to callable signatures, model fields, and public members for",
        "this release. Use the capability pages to see how the objects work together.",
    ]
    seen_anchors: dict[str, int] = {}
    for module_name, slug, title, names in modules:
        output.extend(
            (
                "",
                f"## `{module_name}`",
                "",
                f"[Open the {title} API reference](api/{slug}.md).",
                "",
            )
        )
        for name in names:
            # Preserve qualified legacy fragments and provide compact direct
            # links to the complete symbol entry on its module page.
            base_anchor = _heading_slug(name)
            duplicate_index = seen_anchors.get(base_anchor, 0)
            seen_anchors[base_anchor] = duplicate_index + 1
            legacy_anchor = (
                base_anchor
                if duplicate_index == 0
                else f"{base_anchor}-{duplicate_index}"
            )
            output.append(
                f'- <a id="{module_name}.{name}"></a><a id="{legacy_anchor}"></a>'
                f"[{_code_span(name)}](api/{slug}.md#{base_anchor})"
            )
    return "\n".join(output).rstrip() + "\n"


def _expected_module_paths(
    modules: list[tuple[str, str, str, tuple[str, ...]]],
) -> set[Path]:
    return {MODULE_OUTPUT_DIR / f"{slug}.md" for _, slug, _, _ in modules}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    modules = module_inventory()
    rendered = render()
    expected_paths = _expected_module_paths(modules)
    rendered_modules = {
        MODULE_OUTPUT_DIR / f"{slug}.md": render_module(module_name, names)
        for module_name, slug, _, names in modules
    }
    if args.check:
        stale = not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered
        for path, content in rendered_modules.items():
            stale = (
                stale
                or not path.is_file()
                or path.read_text(encoding="utf-8") != content
            )
        actual_paths = (
            set(MODULE_OUTPUT_DIR.glob("*.md")) if MODULE_OUTPUT_DIR.exists() else set()
        )
        if actual_paths != expected_paths:
            stale = True
        if stale:
            print(
                f"generated Python API reference is stale under {OUTPUT.parent.relative_to(ROOT)}",
                file=sys.stderr,
            )
            return 1
        print("Python API reference is current")
        return 0
    MODULE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    for path, content in rendered_modules.items():
        path.write_text(content, encoding="utf-8")
    for path in set(MODULE_OUTPUT_DIR.glob("*.md")) - expected_paths:
        path.unlink()
    print(f"Rendered Python API reference under {OUTPUT.parent.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
