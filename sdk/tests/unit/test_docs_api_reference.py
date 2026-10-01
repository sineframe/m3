"""Regression checks for the generated public API inventory."""

from __future__ import annotations

from scripts.render_docs_api_reference import render


def test_api_inventory_includes_m3_surface_without_pydantic_internals() -> None:
    rendered = render()

    assert "`trace_view` (property)" in rendered
    assert "(default factory: `builtins.dict`)" in rendered
    assert "`COMPLETED` = `'completed'`" in rendered
    assert "model_extra" not in rendered
    assert "model_fields_set" not in rendered
