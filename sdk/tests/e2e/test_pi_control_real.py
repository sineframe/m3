"""Real Pi 0.85.1 gate for the private M3 control connection."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from m3.harness.pi_control import (
    CONTROL_PROTOCOL_VERSION,
    PiControlChannel,
    PiControlClosed,
    PiControlProtocolError,
)

pytestmark = [pytest.mark.e2e, pytest.mark.process_lifecycle]

_ROOT = Path(__file__).parents[2]
_EXTENSION = _ROOT / "src" / "m3" / "harness" / "pi_extension" / "extension.ts"
_ROUNDTRIP_EXTENSION = Path(__file__).with_name("pi_control_roundtrip_extension.ts")
_CANCEL_EXTENSION = Path(__file__).with_name("pi_control_cancel_extension.ts")
_ACTION_RESET_EXTENSION = Path(__file__).with_name(
    "pi_control_action_reset_extension.ts"
)
_HANDOFF_EXTENSION = Path(__file__).with_name("pi_control_handoff_extension.ts")
_REPLAY_EXTENSION = Path(__file__).with_name("pi_control_replay_extension.ts")
_BRIDGE = _EXTENSION.with_name("bridge.py")


def _require_pi() -> str:
    executable = os.environ.get("M3_PI_EXECUTABLE") or shutil.which("pi")
    if executable is None:
        pytest.skip("Pi 0.85.1 is unavailable on PATH")
    try:
        version = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("Pi 0.85.1 could not be executed")
    if version != "0.85.1":
        pytest.skip(f"Pi version 0.85.1 is required; found {version or 'unknown'}")
    return executable


@pytest.mark.asyncio
async def test_real_pi_bundled_extension_connects_to_control_channel() -> None:
    executable = _require_pi()

    async def scenario() -> None:
        channel = PiControlChannel(timeout=8)
        await channel.start()
        environment = os.environ.copy()
        environment.update(channel.environment)
        environment.update(
            {
                "M3_PI_BRIDGE_COMMAND": sys.executable,
                "M3_PI_BRIDGE_ARGV": json.dumps([str(_BRIDGE)]),
                "M3_MCP_CONFIG": "{}",
                "PI_SKIP_VERSION_CHECK": "1",
                "PI_TELEMETRY": "0",
            }
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(_EXTENSION),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            await channel.wait_connected(8)
            assert channel._connection is not None  # transport parser gate only
            control_writer = channel._connection.writer
            frames = []
            for index in range(140):
                frames.append(
                    json.dumps(
                        {
                            "type": "close",
                            "session_id": channel.session_id,
                            "message_id": f"close-{index}",
                            "reason": ("😀" if index == 0 else "r")
                            + ("x" * (510 if index == 0 else 511)),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                    + b"\n"
                )
            combined = b"".join(frames)
            marker = "😀".encode()
            split = combined.index(marker) + 1
            control_writer.write(combined[:split])
            await control_writer.drain()
            control_writer.write(combined[split:])
            control_writer.write(
                (
                    json.dumps(
                        {
                            "type": "hello",
                            "version": CONTROL_PROTOCOL_VERSION,
                            "session_id": channel.session_id,
                            "token": channel.token,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
            )
            await control_writer.drain()
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(b'{"type":"get_state","id":"control-gate"}\n')
            await process.stdin.drain()
            response: dict[str, object] | None = None
            while response is None or response.get("id") != "control-gate":
                line = await asyncio.wait_for(process.stdout.readline(), 8)
                assert line, "Pi exited before the control gate response"
                value = json.loads(line)
                if isinstance(value, dict):
                    response = value
            assert response["type"] == "response"
            assert response["success"] is True
        finally:
            await channel.close("real_gate_done")
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 8)

    await scenario()


@pytest.mark.asyncio
async def test_real_pi_extension_client_round_trip_advances_two_rounds() -> None:
    executable = _require_pi()

    async def scenario() -> None:
        channel = PiControlChannel(timeout=8)
        await channel.start()
        environment = os.environ.copy()
        environment.update(channel.environment)
        environment.update(
            {"M3_MCP_CONFIG": "{}", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(_ROUNDTRIP_EXTENSION),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            await channel.wait_connected(8)
            first = await channel.receive(8)
            assert first["type"] == "pending"
            assert first["logical_operation_id"] == "op-1"
            assert first["operation_parameters"] == {"weight_kg": 2}
            await channel.send_response(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r1",
                responses={"address": {"action": "accept"}},
            )
            second = await channel.receive(8)
            assert second["round_index"] == 1
            assert second["request_state"] == ""
            await channel.send_response(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r2",
                responses={"address": {"action": "accept"}},
            )
            terminal = await channel.receive(8)
            assert terminal["type"] == "terminal"
            assert terminal["state"] == "delivered"
        finally:
            await channel.close("real_round_trip_done")
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 8)

    await scenario()


@pytest.mark.asyncio
async def test_real_pi_extension_client_cancellation_closes_scope() -> None:
    executable = _require_pi()

    async def scenario() -> None:
        channel = PiControlChannel(timeout=8)
        await channel.start()
        environment = os.environ.copy()
        environment.update(channel.environment)
        environment.update(
            {"M3_MCP_CONFIG": "{}", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(_CANCEL_EXTENSION),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            await channel.wait_connected(8)
            first = await channel.receive(8)
            assert first["logical_operation_id"] == "op-1"
            await channel.send_response(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r1",
                responses={"address": {"action": "accept"}},
            )
            await channel.send_cancel(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r1",
                reason="user_cancelled",
            )
            with pytest.raises(PiControlProtocolError):
                await channel.send_response(
                    generation="g1",
                    turn_sequence=2,
                    execution_id="execution-1",
                    logical_operation_id="op-1",
                    round_id="r1",
                    responses={"address": {"action": "accept"}},
                )
            next_operation = await channel.receive(8)
            assert next_operation["logical_operation_id"] == "op-2"
            assert next_operation["round_index"] == 0
        finally:
            await channel.close("real_cancel_done")
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 8)

    await scenario()


@pytest.mark.asyncio
async def test_real_pi_extension_client_resets_for_second_action() -> None:
    executable = _require_pi()

    async def scenario() -> None:
        channel = PiControlChannel(timeout=8)
        await channel.start()
        environment = os.environ.copy()
        environment.update(channel.environment)
        environment.update(
            {"M3_MCP_CONFIG": "{}", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(_ACTION_RESET_EXTENSION),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            await channel.wait_connected(8)
            first = await channel.receive(8)
            assert first["generation"] == "g1"
            await channel.send_response(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r1",
                responses={"address": {"action": "accept"}},
            )
            assert (await channel.receive(8))["type"] == "terminal"
            channel.set_scope(None)
            second = await channel.receive(8)
            assert second["generation"] == "g2"
            assert second["turn_sequence"] == 3
            await channel.send_response(
                generation="g2",
                turn_sequence=3,
                execution_id="execution-1",
                logical_operation_id="op-2",
                round_id="r2",
                responses={"address": {"action": "accept"}},
            )
            assert (await channel.receive(8))["type"] == "terminal"
            assert channel._connection is not None
        finally:
            await channel.close("real_action_reset_done")
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 8)

    await scenario()


@pytest.mark.asyncio
async def test_real_pi_extension_client_cancellation_handoff_is_not_dropped() -> None:
    executable = _require_pi()

    async def scenario() -> None:
        channel = PiControlChannel(timeout=8)
        await channel.start()
        environment = os.environ.copy()
        environment.update(channel.environment)
        environment.update(
            {"M3_MCP_CONFIG": "{}", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(_HANDOFF_EXTENSION),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            await channel.wait_connected(8)
            frame = await channel.receive(8)
            assert frame["type"] == "pending"
            await channel.send_response(
                generation="g1",
                turn_sequence=2,
                execution_id="execution-1",
                logical_operation_id="op-1",
                round_id="r1",
                responses={"address": {"action": "accept"}},
            )
            terminal = await channel.receive(8)
            assert terminal["state"] == "delivered"
        finally:
            await channel.close("real_handoff_done")
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 8)

    await scenario()


@pytest.mark.asyncio
async def test_real_pi_extension_client_replay_cache_is_bounded() -> None:
    executable = _require_pi()

    async def scenario() -> None:
        channel = PiControlChannel(timeout=8)
        await channel.start()
        environment = os.environ.copy()
        environment.update(channel.environment)
        environment.update(
            {"M3_MCP_CONFIG": "{}", "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            "--mode",
            "rpc",
            "--no-extensions",
            "--no-session",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--extension",
            str(_REPLAY_EXTENSION),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            await channel.wait_connected(8)
            for index in range(130):
                pending = await channel.receive(8)
                assert pending["round_id"] == f"r-{index}"
                response = {
                    "type": "response",
                    "session_id": channel.session_id,
                    "message_id": f"response-{index}",
                    "generation": "g1",
                    "turn_sequence": 2,
                    "execution_id": "execution-1",
                    "logical_operation_id": f"op-{index}",
                    "round_id": f"r-{index}",
                    "responses": {"address": {"action": "accept"}},
                }
                await channel.send(response)
                if index < 129:
                    assert (await channel.receive(8))["type"] == "terminal"
                else:
                    assert channel._connection is not None
                    channel._connection.writer.write(
                        (json.dumps(response, separators=(",", ":")) + "\n").encode()
                    )
                    await channel._connection.writer.drain()
                    with pytest.raises(PiControlClosed):
                        await channel.receive(2)
        finally:
            await channel.close("real_replay_done")
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 8)

    await scenario()
