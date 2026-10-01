"""Execute the public SecretReference value shown in its reference page."""

from __future__ import annotations

import re
from pathlib import Path

from m3.types import SecretReference

_ROOT = Path(__file__).parents[3]


def test_credentials_reference_constructs_the_public_value() -> None:
    page = (_ROOT / "docs/site/reference/credentials.md").read_text(encoding="utf-8")
    match = re.search(r"```python\n(.*?)\n```", page, flags=re.DOTALL)
    assert match is not None
    namespace: dict[str, object] = {}
    exec(compile(match.group(1), "reference/credentials.md", "exec"), namespace)
    value = namespace["endpoint_key"]
    assert isinstance(value, SecretReference)
    assert value.source == "environment"
    assert value.name == "MCP_ENDPOINT_KEY"
