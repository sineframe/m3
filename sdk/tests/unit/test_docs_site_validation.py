"""Regression checks for Markdown code in the published documentation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_VALIDATOR_PATH = _ROOT / "scripts" / "validate_docs_site.py"
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "m3_docs_site_validator", _VALIDATOR_PATH
)
if _VALIDATOR_SPEC is None or _VALIDATOR_SPEC.loader is None:
    raise RuntimeError("could not load the documentation site validator")
_VALIDATOR = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(_VALIDATOR)


def _escaped_angle_count(markdown: str) -> int:
    return _VALIDATOR.escaped_angle_count(markdown)


def test_output_terms_extracts_m3_line_prefixes() -> None:
    plugin_source = """
reporter.write_line(f"M3 run {x}")
reporter.write_line("M3 " + "x")
reporter.write_line("M3 execution timeout: " f"id={x}")
reporter.write_line("M3: no tests executed; skipped-only runs fail")
"""

    lines, _ = _VALIDATOR.output_terms(plugin_source, "")

    assert lines == [
        "M3",
        "M3 execution timeout",
        "M3 run",
        "M3: no tests executed; skipped-only runs fail",
    ]


def test_output_terms_extracts_allowed_limitations() -> None:
    observations_source = '_ALLOWED_LIMITATIONS = frozenset({"b", "a"})'

    _, limitations = _VALIDATOR.output_terms("", observations_source)

    assert limitations == ["a", "b"]


def test_code_regions_find_inline_and_fenced_entities_but_skip_prose() -> None:
    markdown = """Use &lt; in prose when describing an HTML entity.

Inline: `value &gt; 0`; double ticks: `` `value` &amp;lt; limit ``.

Widget(value=&lt;factory&gt;)

```python
value = "&#60;"
```

~~~html
&gt;
~~~
"""

    assert _escaped_angle_count(markdown) == 6


def test_heading_anchors_match_vitepress_and_keep_explicit_ids() -> None:
    markdown = """# `EVENT_SCHEMA_ID`

## `one_of`

## `__version__`

## `3.0 Release`

## Café

<a id="legacy_symbol"></a>
"""

    assert _VALIDATOR.heading_anchors(markdown) == {
        "event-schema-id",
        "one-of",
        "version",
        "_3-0-release",
        "cafe",
        "legacy_symbol",
    }


def test_published_docs_have_no_escaped_angles_in_markdown_code() -> None:
    site = _ROOT / "docs" / "site"
    offenders = {
        path.relative_to(site).as_posix(): _escaped_angle_count(
            path.read_text(encoding="utf-8")
        )
        for path in site.rglob("*.md")
        if not path.is_symlink()
    }
    offenders = {path: count for path, count in offenders.items() if count}

    assert offenders == {}
