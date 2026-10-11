"""Regression checks for links in generated skill references."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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


def test_unknown_page_kind_is_rejected() -> None:
    page = {**_RENDERER._selected_pages()[0], "kind": "new-kind"}

    with pytest.raises(ValueError, match="unknown documentation kind 'new-kind'"):
        _RENDERER._render_references([page])


def test_every_generated_reference_is_in_the_index() -> None:
    rendered = _RENDERER._render_references(_RENDERER._selected_pages())
    index = rendered[_RENDERER.REFERENCES / "index.md"]

    for path in rendered:
        if path.parent == _RENDERER.REFERENCES and path.name != "index.md":
            assert f"]({path.name})" in index


def test_unknown_page_kind_stops_generation_before_writing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    page = {**_RENDERER._selected_pages()[0], "kind": "new-kind"}
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        _RENDERER.SKILL_FILE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(_RENDERER, "_selected_pages", lambda: [page])
    monkeypatch.setattr(_RENDERER, "SKILL_DIR", tmp_path)
    monkeypatch.setattr(_RENDERER, "SKILL_FILE", skill_file)
    monkeypatch.setattr(_RENDERER, "EXAMPLES", tmp_path / "examples")
    monkeypatch.setattr(_RENDERER, "REFERENCES", tmp_path / "references")
    monkeypatch.setattr("sys.argv", [str(_SCRIPT_PATH)])

    assert _RENDERER.main() == 1
    assert "unknown documentation kind 'new-kind'" in capsys.readouterr().out
    assert not (tmp_path / "references").exists()
