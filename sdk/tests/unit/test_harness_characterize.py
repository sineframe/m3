from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest

import m3.harness.characterize as characterize_module
from m3.harness.acp import AcpHarnessAdapter
from m3.harness.characterize import MAX_PROBE_OUTPUT, _run_probe
from m3.harness.claude import ClaudeCodeHarnessAdapter
from m3.harness.codex import CodexHarnessAdapter
from m3.harness.contracts import HarnessInteractionCapabilities
from m3.harness.opencode import OpenCodeHarnessAdapter
from m3.harness.pi import PiHarnessAdapter


def _executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_probe_retains_capabilities_after_legacy_short_prefix(tmp_path: Path) -> None:
    executable = _executable(
        tmp_path / "help-fixture.py",
        "print('x' * 700 + ' --input-format stream-json --output-format stream-json')\n",
    )

    output = _run_probe(executable, ("--help",), {"PATH": "/usr/bin"})

    assert len(output) <= MAX_PROBE_OUTPUT
    assert "stream-json" in output


def test_native_harness_interaction_evidence_is_not_inferred_from_help() -> None:
    """Help/version probes do not prove a modern MRTR interaction API."""

    for harness in ("codex", "claude", "opencode", "acp"):
        assert characterize_module.interaction_capabilities_for(harness) == (
            HarnessInteractionCapabilities()
        )
    assert characterize_module.interaction_capabilities_for("pi").retry_owner == "m3"


def test_real_adapter_declarations_remain_explicit() -> None:
    adapters = (
        (PiHarnessAdapter(executable="pi"), "m3"),
        (CodexHarnessAdapter(executable="codex"), "harness"),
        (ClaudeCodeHarnessAdapter(executable="claude"), "harness"),
        (AcpHarnessAdapter(), "harness"),
        (OpenCodeHarnessAdapter(executable="opencode"), "harness"),
    )

    for adapter, retry_owner in adapters:
        capabilities = adapter.capabilities.interaction
        expected = adapter.__class__ is PiHarnessAdapter
        assert capabilities.supports_elicitation is expected
        assert capabilities.preserves_request_keys is expected
        assert capabilities.preserves_multi_request_rounds is expected
        assert capabilities.supports_interaction_cancellation is expected
        assert capabilities.retry_owner == retry_owner


@pytest.mark.parametrize(
    ("harness", "args"),
    (
        ("pi", ("--version",)),
        ("codex", ("--version",)),
        ("claude", ("--version",)),
        ("opencode", ("--version",)),
    ),
)
def test_installed_binary_version_probe_keeps_explicit_mrtr_declaration(
    harness: str, args: tuple[str, ...]
) -> None:
    executable = shutil.which(harness)
    if executable is None:
        pytest.skip(f"{harness} is not installed")

    with tempfile.TemporaryDirectory(prefix="m3-characterize-test-") as home:
        output = _run_probe(
            executable,
            args,
            {"PATH": os.environ.get("PATH", ""), "HOME": home},
        )

    assert output
    supported = harness == "pi"
    assert (
        characterize_module.interaction_capabilities_for(harness).supports_elicitation
        is supported
    )
