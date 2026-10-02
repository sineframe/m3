"""Regression checks for the generated public API inventory."""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

_RENDERER_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "render_docs_api_reference.py"
)
_RENDERER_SPEC = importlib.util.spec_from_file_location(
    "m3_docs_api_reference_renderer", _RENDERER_PATH
)
if _RENDERER_SPEC is None or _RENDERER_SPEC.loader is None:
    raise RuntimeError("could not load the documentation API renderer")
_RENDERER = importlib.util.module_from_spec(_RENDERER_SPEC)
_RENDERER_SPEC.loader.exec_module(_RENDERER)
render = _RENDERER.render


def test_api_inventory_includes_m3_surface_without_pydantic_internals() -> None:
    rendered = render()

    modules = _RENDERER.module_inventory()
    module_pages = [
        _RENDERER.render_module(module_name, names)
        for module_name, _, _, names in modules
    ]
    all_reference = rendered + "\n".join(module_pages)

    assert "trace_view" in all_reference
    assert "factory builtins.dict()" in all_reference
    assert "COMPLETED" in all_reference and "'completed'" in all_reference
    assert "model_extra" not in rendered
    assert "model_fields_set" not in rendered


def test_api_reference_uses_readable_multiline_signatures_and_model_tables() -> None:
    modules = _RENDERER.module_inventory()
    pages = [
        _RENDERER.render_module(module_name, names)
        for module_name, _, _, names in modules
    ]
    all_reference = "\n".join(pages)

    assert "```python\n" in all_reference
    assert (
        "| Field | Type | Required | Default | Constraints | Description |"
        in all_reference
    )
    assert "FieldInfo(" not in all_reference
    assert "<class '" not in all_reference
    assert "tuple[int, ...]" in all_reference
    assert " = ...," in all_reference
    code_blocks = re.findall(r"```[^\n]*\n(.*?)```", all_reference, flags=re.DOTALL)
    inline_code = re.findall(r"(`+)(.*?)\1", all_reference, flags=re.DOTALL)
    code_content = "\n".join(code_blocks + [content for _, content in inline_code])
    assert "&lt;" not in code_content
    assert "&gt;" not in code_content
    assert "&amp;" not in code_content


def test_api_landing_links_symbols_without_repeating_symbol_headings() -> None:
    rendered = render()

    assert "[`AgentSession`](api/sync-api.md#agentsession)" in rendered
    assert '<a id="m3.sync_api.AgentSession"></a>' in rendered
    assert '<a id="agentsession"></a>' in rendered
    assert "### `AgentSession`" not in rendered


def test_signature_format_keeps_parameter_separators() -> None:
    def sample(a: int, /, b: str, *, c: bool = False, d: int = 1) -> None:
        return None

    block = _RENDERER._signature_block("sample", sample)

    assert block.count("    *,") == 1
    assert "    /," in block
    assert "    c: bool = False," in block
    assert "    d: int = 1," in block

    def mixed(a: int, /, *, b: str) -> None:
        return None

    mixed_block = _RENDERER._signature_block("mixed", mixed)
    assert mixed_block.index("    /,") < mixed_block.index("    *,")
    assert _RENDERER._signature_value_repr(set()) == "set()"
    assert _RENDERER._signature_value_repr(frozenset()) == "frozenset()"


def test_nested_field_constraints_and_table_code_spans_are_preserved() -> None:
    from annotated_types import Ge, MinLen

    nested = Annotated[list[Annotated[int, Ge(ge=0)]], MinLen(min_length=1)]
    field = SimpleNamespace(
        annotation=nested,
        metadata=(),
        discriminator=None,
        default=inspect.Parameter.empty,
        default_factory=None,
        is_required=lambda: True,
    )

    constraints = _RENDERER._field_constraints(field)
    assert "min_length=1" in constraints
    assert "items: ge=0" in constraints
    assert _RENDERER._code_span("A | B", in_table=True) == "`A \\| B`"
    assert _RENDERER._code_span("a`b", in_table=True) == "``a`b``"
