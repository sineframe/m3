"""Regression checks for the generated public API inventory."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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

    assert "`trace_view` (property)" in rendered
    assert "(default factory: `builtins.dict`)" in rendered
    assert "`COMPLETED` = `'completed'`" in rendered
    assert "model_extra" not in rendered
    assert "model_fields_set" not in rendered
