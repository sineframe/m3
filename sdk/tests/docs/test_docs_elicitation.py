"""Focused checks for the elicitation documentation and its source blocks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SITE = ROOT / "docs" / "site"
GUIDE_IDS = (
    "guides-elicitation-plans",
    "guides-elicitation-composed",
    "guides-elicitation-responses",
    "guides-elicitation-agents",
    "guides-elicitation-manual",
    "guides-elicitation-managed-input",
)
PYTHON_FENCE = re.compile(r"```(?:python|py)\s*\n(.*?)\n```", re.DOTALL)


def test_all_owning_guide_blocks_match_their_manifest_sources() -> None:
    navigation = json.loads((SITE / "navigation.json").read_text(encoding="utf-8"))
    page_sources = {page["id"]: SITE / page["source"] for page in navigation["pages"]}
    assert set(GUIDE_IDS) <= set(page_sources)

    projects = ROOT / "sdk" / "examples" / "docs"
    for manifest_path in sorted(projects.glob("elicitation-*/example.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for page_id in manifest["owning_pages"]:
            if page_id not in GUIDE_IDS:
                continue
            page = page_sources[page_id]
            fences = PYTHON_FENCE.findall(page.read_text(encoding="utf-8"))
            blocks = [
                block
                for block in manifest["displayed_blocks"]
                if block["page"] == page_id
            ]
            assert len(fences) == len(blocks), f"{page}: Python fence count changed"
            for fence, block in zip(fences, blocks, strict=True):
                assert block["kind"] == "file", f"{page}: unexpected snippet block"
                source = (manifest_path.parent / block["source"]).read_text(
                    encoding="utf-8"
                )
                assert fence.strip() == source.strip(), (
                    f"{page}: displayed block {block['label']!r} differs from "
                    f"{block['source']}"
                )


def test_public_elicitation_reference_builder_example_runs() -> None:
    page = SITE / "reference" / "python" / "m3" / "elicitation.md"
    source = PYTHON_FENCE.findall(page.read_text(encoding="utf-8"))[0]
    namespace: dict[str, Any] = {"__name__": "__docs_elicitation_reference__"}
    exec(compile(source, str(page), "exec"), namespace)

    plan = namespace["plan"]
    assert plan.is_complete
    assert plan.node == "sequence"
    assert plan.children[0].request.request_key == "shipping_address"
    assert plan.children[1].request.request_key == "identity_check"
    assert plan.children[0].response.content == {
        "street": "1 Main Street",
        "city": "Pune",
    }


def test_managed_input_reference_has_a_named_native_execution_test() -> None:
    source = ROOT / "sdk" / "tests" / "e2e" / "test_docs_managed_input_reference.py"
    text = source.read_text(encoding="utf-8")
    assert (
        "def test_managed_input_reference_fence_against_native_codex_and_local_provider("
        in text
    )
    assert (
        "def test_managed_input_project_test_against_native_codex_and_local_provider("
        in text
    )
