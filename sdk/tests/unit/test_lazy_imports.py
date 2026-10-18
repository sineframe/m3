"""Startup-critical entry points must not load the SDK or MCP protocol models."""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import m3
from m3._exports import PUBLIC_EXPORTS

_SRC = Path(__file__).parents[2].resolve() / "src"

_HEAVY_MODULES = (
    "m3.sync_api",
    "m3.types",
    "m3.policy",
    "m3.observability",
    "m3.transport.capture_proxy",
    "m3.transport.http_proxy",
    "m3.trace.claude",
    "mcp",
    "mcp_types",
    "pydantic",
)


def _run(script: str) -> object:
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(_SRC)},
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _static_root_namespace() -> dict[str, object]:
    """Execute the ``TYPE_CHECKING`` block of ``m3/__init__.py`` eagerly."""

    assert m3.__file__ is not None
    tree = ast.parse(Path(m3.__file__).read_text(encoding="utf-8"))
    block = next(
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "_typing.TYPE_CHECKING"
    )
    namespace: dict[str, object] = {"__name__": "m3", "__package__": "m3"}
    exec(
        compile(ast.Module(body=block.body, type_ignores=[]), m3.__file__, "exec"),
        namespace,
    )
    return namespace


def test_stdio_proxy_import_skips_sdk_and_protocol_models() -> None:
    script = (
        "import json, sys\n"
        "import m3.transport.stdio_proxy\n"
        f"heavy = {_HEAVY_MODULES!r}\n"
        "print(json.dumps(sorted(n for n in heavy if n in sys.modules)))\n"
    )

    assert _run(script) == []


def test_root_names_resolve_on_first_access() -> None:
    script = (
        "import json, sys\n"
        "import m3\n"
        "from m3 import _timing\n"
        "before = sorted(n for n in ('m3.sync_api', 'm3.observability', 'm3.types')\n"
        "                if n in sys.modules)\n"
        "kit = m3.MCPTestKit\n"
        "from m3.sync_api import MCPTestKit\n"
        "print(json.dumps({\n"
        "    'before': before,\n"
        "    'same': kit is MCPTestKit,\n"
        "    'submodule': m3.types.__name__,\n"
        "    'all': list(m3.__all__),\n"
        "    'in_dir': 'FullToolPolicy' in dir(m3),\n"
        "    'missing': hasattr(m3, 'DefinitelyMissing'),\n"
        "}))\n"
    )
    state = _run(script)

    assert state == {
        "before": [],
        "same": True,
        "submodule": "m3.types",
        "all": list(PUBLIC_EXPORTS["m3"]),
        "in_dir": True,
        "missing": False,
    }


def test_lazy_root_matches_static_declarations() -> None:
    namespace = _static_root_namespace()
    reference = {
        name: value
        for name, value in namespace.items()
        if not name.startswith("_") and not inspect.ismodule(value)
    }
    lazy_attrs: dict[str, str] = m3._LAZY_ATTRS  # type: ignore[attr-defined]
    submodules: frozenset[str] = m3._SUBMODULES  # type: ignore[attr-defined]

    assert set(lazy_attrs) == set(reference)
    mismatched = sorted(
        name for name, value in reference.items() if getattr(m3, name) is not value
    )
    assert mismatched == []
    assert submodules.isdisjoint(lazy_attrs)
    assert m3.__version__ == namespace["__version__"]
    assert list(m3.__all__) == namespace["__all__"]
