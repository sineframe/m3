"""Regression checks for links in generated skill references."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _ROOT / "scripts" / "render_skill_references.py"
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "m3_render_skill_references", _SCRIPT_PATH
)
if _SCRIPT_SPEC is None or _SCRIPT_SPEC.loader is None:
    raise RuntimeError("could not load the skill reference renderer")
_RENDERER = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_RENDERER)


def test_selected_page_link_is_flattened_with_fragment() -> None:
    result = _RENDERER.rewrite_links(
        "[Guide](../guides/guide.md#frag)",
        "reference/index.md",
        {"guides/guide.md": "guide"},
    )

    assert result == "[Guide](guide.md#frag)"


def test_excluded_page_link_uses_hosted_docs() -> None:
    result = _RENDERER.rewrite_links(
        "[API](python/api/symbol.md#usage)",
        "reference/index.md",
        {},
    )

    assert (
        result
        == "[API](https://m3.sineframe.com/docs/reference/python/api/symbol#usage)"
    )


def test_repository_link_is_explained_as_repository_path() -> None:
    rendered = _RENDERER.rewrite_links(
        "Read [source](../../../../sdk/README.md).\n",
        "guides/agents/skill.md",
        {},
    )

    assert rendered == "Read source (M3 repository: `sdk/README.md`).\n"


def test_links_in_fenced_code_are_untouched() -> None:
    text = "```md\n[Guide](../guide.md)\n```\n"

    assert _RENDERER.rewrite_links(text, "reference/index.md", {}) == text


def test_links_in_inline_code_are_untouched() -> None:
    text = "Use `[Guide](../guide.md)` literally."

    assert _RENDERER.rewrite_links(text, "reference/index.md", {}) == text


def test_inline_code_in_selected_link_label_is_rewritten() -> None:
    result = _RENDERER.rewrite_links(
        "[`tools`](../results/traces.md)",
        "guides/agents/first-test.md",
        {"guides/results/traces.md": "guides-results-traces"},
    )

    assert result == "[`tools`](guides-results-traces.md)"


def test_inline_code_in_repository_link_label_is_preserved() -> None:
    result = _RENDERER.rewrite_links(
        "[`server` source](../../../../sdk/README.md)",
        "guides/agents/skill.md",
        {},
    )

    assert result == "`server` source (M3 repository: `sdk/README.md`)"


def test_link_with_multiline_label_is_rewritten() -> None:
    result = _RENDERER.rewrite_links(
        "See [test\ntools](../results/traces.md).\n",
        "guides/agents/first-test.md",
        {"guides/results/traces.md": "guides-results-traces"},
    )

    assert result == "See [test\ntools](guides-results-traces.md).\n"


def test_link_title_is_preserved_when_target_is_rewritten() -> None:
    result = _RENDERER.rewrite_links(
        '[Guide](../guides/guide.md "Reference")',
        "reference/index.md",
        {"guides/guide.md": "guide"},
    )

    assert result == '[Guide](guide.md "Reference")'


def test_angle_bracket_target_is_rewritten() -> None:
    result = _RENDERER.rewrite_links(
        "[Guide](<../guides/guide page.md>)",
        "reference/index.md",
        {"guides/guide page.md": "guide"},
    )

    assert result == "[Guide](<guide.md>)"


def test_bundled_query_is_dropped_and_fragment_is_kept() -> None:
    result = _RENDERER.rewrite_links(
        "[Guide](../guides/guide.md?x=1#frag)",
        "reference/index.md",
        {"guides/guide.md": "guide"},
    )

    assert result == "[Guide](guide.md#frag)"


def test_hosted_query_and_fragment_are_kept() -> None:
    result = _RENDERER.rewrite_links(
        "[API](python/api/symbol.md?x=1#usage)",
        "reference/index.md",
        {},
    )

    assert (
        result
        == "[API](https://m3.sineframe.com/docs/reference/python/api/symbol?x=1#usage)"
    )


def test_reference_definition_target_is_rewritten_and_title_kept() -> None:
    result = _RENDERER.rewrite_links(
        '[Guide]: ../guides/guide.md "Reference"\n[Text][Guide]\n',
        "reference/index.md",
        {"guides/guide.md": "guide"},
    )

    assert result == '[Guide]: guide.md "Reference"\n[Text][Guide]\n'


def test_cli_flag_extraction_matches_exact_tokens() -> None:
    available = _RENDERER.cli_flags("Options: --project-name NAME")

    assert "--project-name" in available
    assert "--project" not in available
