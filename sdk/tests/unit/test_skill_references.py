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


def test_footnote_definition_is_left_unchanged() -> None:
    text = "[^1]: Evidence is stored per attempt.\n"

    assert _RENDERER.rewrite_links(text, "reference/index.md", {}) == text


def test_prose_continuation_that_looks_like_definition_is_unchanged() -> None:
    text = "A paragraph continues here:\n[note]: keep this\n"

    assert _RENDERER.rewrite_links(text, "reference/index.md", {}) == text


def test_definition_after_blank_line_is_rewritten_with_title() -> None:
    text = 'A paragraph.\n\n[Guide]: ../guides/guide.md "Reference"\n'

    assert (
        _RENDERER.rewrite_links(
            text, "reference/index.md", {"guides/guide.md": "guide"}
        )
        == 'A paragraph.\n\n[Guide]: guide.md "Reference"\n'
    )


def test_repository_reference_definition_is_preserved_and_linted() -> None:
    text = "[Source]: ../../../sdk/README.md\n"

    assert _RENDERER.rewrite_links(text, "reference/index.md", {}) == text
    assert _RENDERER.reference_definition_errors(
        text, "reference/index.md", {}, "references/example.md"
    ) == [
        "references/example.md reference definition cannot be bundled: "
        "../../../sdk/README.md; use an inline link"
    ]


def test_cli_flag_extraction_matches_exact_tokens() -> None:
    available = _RENDERER.cli_flags("Options: --project-name NAME")

    assert "--project-name" in available
    assert "--project" not in available


_GUIDE = _RENDERER.REFERENCES / "guide.md"
_GUIDE_TEXT = "# Guide\n\n## Real heading\n"


def _skill(body: str, description: str = "description: Use when testing.") -> str:
    return f"---\nname: testing-with-m3\n{description}\n---\n\n{body}\n"


def _lint(skill_text: str) -> list[str]:
    expected = {_RENDERER.SKILL_FILE: skill_text, _GUIDE: _GUIDE_TEXT}
    return _RENDERER._lint(skill_text, expected, [])


def test_skill_link_to_existing_heading_passes() -> None:
    text = _skill("# Top\n\n[Guide](references/guide.md#real-heading) [Up](#top)")

    assert _lint(text) == []


def test_skill_link_to_missing_heading_is_rejected() -> None:
    text = _skill("[Guide](references/guide.md#no-such-heading)")

    assert _lint(text) == [
        "SKILL.md link anchor does not resolve: references/guide.md#no-such-heading"
    ]


def test_skill_in_page_link_to_missing_heading_is_rejected() -> None:
    assert _lint(_skill("# Top\n\n[Nope](#nope)")) == [
        "SKILL.md link anchor does not resolve: #nope"
    ]


def test_skill_fragment_on_non_markdown_target_is_not_checked() -> None:
    expected = {
        _RENDERER.SKILL_FILE: _skill("[Code](examples/a/test_a.py#L1)"),
        _RENDERER.EXAMPLES / "a" / "test_a.py": "# not a heading\n",
    }

    assert _RENDERER._lint(expected[_RENDERER.SKILL_FILE], expected, []) == []


def test_reference_link_to_missing_heading_is_rejected() -> None:
    other = _RENDERER.REFERENCES / "other.md"
    expected = {
        other: "[Guide](guide.md#no-such-heading) [Ok](guide.md#real-heading)\n",
        _GUIDE: _GUIDE_TEXT,
    }

    assert _RENDERER._reference_link_errors(expected, []) == [
        "references/other.md link anchor does not resolve: guide.md#no-such-heading"
    ]


def test_reference_in_page_link_to_missing_heading_is_rejected() -> None:
    expected = {_GUIDE: _GUIDE_TEXT + "\n[Here](#real-heading) [Gone](#gone)\n"}

    assert _RENDERER._reference_link_errors(expected, []) == [
        "references/guide.md link anchor does not resolve: #gone"
    ]


def test_block_scalar_description_is_rejected() -> None:
    text = _skill("body", "description: >\n  Use when testing\n  with M3.")

    errors = _lint(text)

    assert (
        "SKILL.md frontmatter 'description' must be a single-line value, not a block scalar"
        in errors
    )
    assert any("unsupported line" in error for error in errors)


def test_literal_block_description_is_rejected() -> None:
    errors = _lint(_skill("body", "description: |\n  Use when testing."))

    assert any("not a block scalar" in error for error in errors)
