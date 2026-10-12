from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3._configuration_context import install_configuration, restore_configuration
from m3._configuration_run import ConfigurationInvocation, accept_configuration_transfer
from m3._configuration_snapshot import ConfigurationSnapshotError
from m3._pytest_configuration import InvocationConfigurationPlugin
from m3.configuration import Config, load_config
from m3.sync_api import MCPTestKit
from m3.types import HarnessTimeouts

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX private transfer")


def test_bootstrapped_kits_survive_controller_close_and_source_deletion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "settings.toml"
    source.write_text("[tool.m3.harness_timeouts]\nstartup_seconds=90\n")
    invocation = ConfigurationInvocation(load_config(env={}, config_file=source))
    context = None
    try:
        transfer = invocation.issue()
        context = accept_configuration_transfer(transfer.model_dump_json())
        source.unlink()
        invocation.close()
        token = install_configuration(context)
        try:
            for _ in range(2):
                with MCPTestKit(
                    {"harness_timeouts": HarnessTimeouts(initialize_seconds=15)},
                    embedded_worker=False,
                ) as kit:
                    assert kit.config.harness_timeouts.startup_seconds == 90
                    assert kit.config.harness_timeouts.initialize_seconds == 15
                    assert kit._literal_registry is context.literal_registry
        finally:
            restore_configuration(token)
    finally:
        invocation.close()
        if context is not None and context.literal_registry is not None:
            context.literal_registry.close()


def test_controller_close_before_bootstrap_fails_without_source_fallback(
    tmp_path: Path,
) -> None:
    source = tmp_path / "settings.toml"
    source.write_text("[tool.m3.harness_timeouts]\nstartup_seconds=90\n")
    invocation = ConfigurationInvocation(load_config(env={}, config_file=source))
    try:
        transfer = invocation.issue()
        invocation.close()
        source.write_text("[tool.m3.harness_timeouts]\nstartup_seconds=1\n")
        with pytest.raises(ConfigurationSnapshotError, match="unavailable"):
            accept_configuration_transfer(transfer.model_dump_json())
    finally:
        invocation.close()


@pytest.mark.parametrize(
    "spec",
    [
        {"popen": False, "ssh": None, "socket": None, "via": None},
        {"popen": True, "ssh": "remote", "socket": None, "via": None},
        {"popen": True, "ssh": None, "socket": "remote", "via": None},
        {"popen": True, "ssh": None, "socket": None, "via": "remote"},
    ],
)
def test_private_configuration_rejects_remote_workers_before_issuing_capability(
    spec: dict[str, object],
) -> None:
    invocation = ConfigurationInvocation(
        Config(harness_timeouts=HarnessTimeouts(startup_seconds=90))
    )
    plugin = InvocationConfigurationPlugin(invocation.context)
    node = SimpleNamespace(
        gateway=SimpleNamespace(spec=SimpleNamespace(**spec)),
        workerinput={"mainargv": []},
    )
    try:
        with pytest.raises(pytest.UsageError, match="local workers"):
            plugin.pytest_configure_node(node)
        assert plugin._invocation._broker is None
        assert "m3_configuration" not in node.workerinput
    finally:
        plugin.close()
        invocation.close()


def test_invalid_transfer_cannot_echo_private_values() -> None:
    raw = json.dumps({"version": 1, "snapshot": {"literal": "PRIVATE-CANARY"}})
    with pytest.raises(ConfigurationSnapshotError) as error:
        accept_configuration_transfer(raw)
    assert "PRIVATE-CANARY" not in str(error.value)
