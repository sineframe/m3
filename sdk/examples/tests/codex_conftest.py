"""Deterministic local fixture for runnable Codex MRTR examples."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.codex_responses_provider import (
    ModelOutput,
    ResponsesRun,
    open_codex_responses_provider,
)

from m3 import MCPTestKit
from m3.agent_session import AdapterTurn
from m3.elicitation import ElicitationPlan
from m3.harness import HarnessAdapterRegistry, HarnessLaunch
from m3.harness.codex import CodexHarnessAdapter
from m3.harness.observations import HarnessObservation
from m3.types import Codex, HarnessSpec, StdioServer

SDK_ROOT = Path(__file__).parents[2]
EXAMPLES_ROOT = SDK_ROOT / "examples"
_EXPECTED_CODEX_VERSION = "codex-cli 0.156.1"


def _require_codex() -> str:
    executable = os.environ.get("M3_CODEX_EXECUTABLE") or shutil.which("codex")
    if executable is None:
        pytest.fail("Codex 0.156.1 is required; set M3_CODEX_EXECUTABLE")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.fail("Codex 0.156.1 could not be executed")
    expected = _EXPECTED_CODEX_VERSION
    if version != expected:
        pytest.fail(f"expected {expected!r}, found {version!r}")
    return executable


class FixtureCodexHarnessAdapter(CodexHarnessAdapter):
    """Use the test's local deterministic Responses API endpoint."""

    def __init__(self, *, executable: str, provider_url: str) -> None:
        super().__init__(executable=executable)
        self._provider_url = provider_url
        self.app_server_frames: list[dict[str, Any]] = []
        self.client_frames: list[dict[str, Any]] = []
        self.closed_stderr = ""
        self.last_turn: AdapterTurn | None = None

    async def open(self, launch: HarnessLaunch) -> Any:
        try:
            return await super().open(launch)
        except Exception as exc:
            # The public session result intentionally hides startup details.
            # Keep the adapter's sanitized exception visible in CI failures.
            print(f"Codex fixture startup: {type(exc).__name__}: {exc}")
            raise

    async def send(
        self,
        message: Any,
        *,
        timeout: float | None = None,
        metadata: Mapping[str, object] | None = None,
        elicitation: ElicitationPlan | None = None,
        elicitation_round_limit: int = 10,
    ) -> AdapterTurn:
        self.last_turn = await super().send(
            message,
            timeout=timeout,
            metadata=metadata,
            elicitation=elicitation,
            elicitation_round_limit=elicitation_round_limit,
        )
        return self.last_turn

    def consume_frame(
        self,
        frame: Mapping[str, Any],
        sequence: int,
        wall: datetime,
        started: float,
        observations: list[HarnessObservation],
    ) -> tuple[bool, str, list[Mapping[str, Any]]]:
        self.app_server_frames.append(dict(frame))
        return super().consume_frame(frame, sequence, wall, started, observations)

    async def _write_frame(self, process: Any, payload: Mapping[str, Any]) -> None:
        self.client_frames.append(dict(payload))
        await super()._write_frame(process, payload)

    async def close(self) -> None:
        process = self._process
        await super().close()
        owner = getattr(process, "owner", None)
        stderr_task = getattr(owner, "stderr_task", None)
        if (
            stderr_task is not None
            and stderr_task.done()
            and not stderr_task.cancelled()
        ):
            try:
                self.closed_stderr = stderr_task.result().decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                self.closed_stderr = "<stderr capture failed>"

    def environment_for_launch(
        self, launch: HarnessLaunch, root: Path
    ) -> dict[str, str]:
        environment = dict(super().environment_for_launch(launch, root))
        codex_home = Path(environment["CODEX_HOME"])
        config_path = codex_home / "config.toml"
        config = config_path.read_text(encoding="utf-8")
        config = (
            'model = "m3-fixture-model"\n'
            'model_provider = "m3-fixture"\n\n'
            + config
            + "\n[model_providers.m3-fixture]\n"
            'name = "m3 local deterministic provider"\n'
            f"base_url = {json.dumps(self._provider_url)}\n"
            'wire_api = "responses"\n'
            "requires_openai_auth = false\n"
            "supports_websockets = false\n"
            "request_max_retries = 0\n"
            "stream_max_retries = 0\n"
        )
        config_path.write_text(config, encoding="utf-8")
        config_path.chmod(0o600)
        return environment


@dataclass(slots=True)
class CodexExample:
    kit: MCPTestKit
    provider: ResponsesRun
    executable: str
    marker: Path
    adapters: list[FixtureCodexHarnessAdapter]

    def enqueue_tool_and_final(self, tool: str, arguments: dict[str, object]) -> None:
        self.provider.enqueue(
            ModelOutput(
                function_name=f"mcp__fixture::{tool}",
                arguments=arguments,
            )
        )
        self.provider.enqueue(ModelOutput(text="completed by deterministic provider"))

    def agent(self) -> Any:
        return self.kit.agents(
            [
                {
                    "harness": "codex",
                    "models": ["m3-fixture-model"],
                    "executable": self.executable,
                }
            ]
        )[0]

    def server(self) -> StdioServer:
        return StdioServer(
            name="fixture",
            command=sys.executable,
            args=(str(SDK_ROOT / "tests" / "fixtures" / "codex_mrtr_server.py"),),
            cwd=str(SDK_ROOT),
            environment={"M3_CODEX_MRTR_WIRE_MARKER": str(self.marker)},
        )

    def wire_calls(self) -> list[dict[str, Any]]:
        if not self.marker.exists():
            return []
        return [
            record["params"]
            for line in self.marker.read_text(encoding="utf-8").splitlines()
            if (record := json.loads(line)).get("method") == "tools/call"
            and isinstance(record.get("params"), dict)
        ]

    def adapter_evidence(self) -> dict[str, object]:
        return {
            "app_server_frames": [
                frame
                for adapter in self.adapters
                for frame in adapter.app_server_frames
            ],
            "m3_client_frames": [
                frame for adapter in self.adapters for frame in adapter.client_frames
            ],
            "adapter_turns": [
                {
                    "outcome": turn.outcome.value,
                    "terminal": turn.terminal,
                    "error": (
                        None
                        if turn.error is None
                        else {
                            "code": turn.error.code.value,
                            "message": turn.error.message,
                            "details": dict(turn.error.details),
                        }
                    ),
                }
                for adapter in self.adapters
                if (turn := adapter.last_turn) is not None
            ],
            "app_server_stderr": [adapter.closed_stderr for adapter in self.adapters],
        }

    def failure_bundle(self, error: object) -> Path:
        path = self.marker.with_name("codex-failure-evidence.json")
        evidence = {
            "error": repr(error),
            "mcp_wire_calls": self.wire_calls(),
            "provider_requests": [
                {
                    "method": request.method,
                    "path": request.path,
                    "headers": dict(request.headers),
                    "body": request.body,
                }
                for request in self.provider.requests
            ],
            "provider_responses": [
                {"status": response.status, "body": response.body}
                for response in self.provider.responses
            ],
            **self.adapter_evidence(),
        }
        path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
        )
        return path


@pytest.fixture
def codex_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[CodexExample]:
    executable = _require_codex()
    isolated_source_home = tmp_path / "empty-codex-source-home"
    isolated_source_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(isolated_source_home))

    provider = ResponsesRun()
    marker = tmp_path / "mcp-wire.jsonl"
    adapters: list[FixtureCodexHarnessAdapter] = []
    with open_codex_responses_provider(provider) as provider_url:

        def make_adapter(harness: HarnessSpec) -> FixtureCodexHarnessAdapter:
            if not isinstance(harness, Codex):
                raise TypeError("Codex example received a non-Codex harness")
            adapter = FixtureCodexHarnessAdapter(
                executable=harness.executable or executable,
                provider_url=provider_url,
            )
            adapters.append(adapter)
            return adapter

        registry = HarnessAdapterRegistry({"codex": make_adapter})
        kit = MCPTestKit(env={}, cwd=str(SDK_ROOT.parent), adapter_registry=registry)
        try:
            yield CodexExample(kit, provider, executable, marker, adapters)
        finally:
            kit.close()


__all__ = ["CodexExample"]
