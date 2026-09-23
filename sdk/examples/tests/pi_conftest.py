"""Typed pytest infrastructure for the local installed-Pi examples."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from m3 import MCPTestKit
from m3.harness import HarnessAdapterRegistry, HarnessLaunch, PiHarnessAdapter

from .pi_provider import ProviderRun, open_provider

EXAMPLES_ROOT = Path(__file__).parents[1]


class ExamplePiAdapter(PiHarnessAdapter):
    def process_argv(self, launch: HarnessLaunch) -> tuple[str, ...]:
        extension = EXAMPLES_ROOT / "servers" / "modern_mrtr_provider.ts"
        return (*super().process_argv(launch), "--extension", str(extension))


@pytest.fixture
def pi_adapter(tmp_path: Path) -> Iterator[tuple[ExamplePiAdapter, Path]]:
    executable = os.environ.get("M3_PI_EXECUTABLE") or shutil.which("pi")
    if executable is None:
        pytest.skip("Pi 0.85.1 is unavailable on PATH; set M3_PI_EXECUTABLE")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Pi 0.85.1 could not be executed at the required runtime")
    if version != "0.85.1":
        pytest.skip(f"Pi version 0.85.1 is required; found {version or 'unknown'}")

    marker = tmp_path / "mcp-wire.jsonl"
    provider_run = ProviderRun()
    with open_provider(provider_run) as provider_url:
        yield (
            ExamplePiAdapter(
                executable=executable,
                environment={
                    "M3_PI_FIXTURE_PROVIDER_URL": provider_url,
                    "M3_MRTR_WIRE_MARKER": str(marker),
                },
            ),
            marker,
        )


@pytest.fixture
def m3_kit(pi_adapter: tuple[ExamplePiAdapter, Path]) -> Iterator[MCPTestKit]:
    adapter, _marker = pi_adapter
    kit = MCPTestKit(
        adapter_registry=HarnessAdapterRegistry({"pi": lambda _harness: adapter})
    )
    try:
        yield kit
    finally:
        kit.close()


@pytest.fixture
def pi_fixture(pi_adapter: tuple[ExamplePiAdapter, Path]) -> Path:
    _adapter, marker = pi_adapter
    return marker
