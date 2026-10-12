"""Resolve invocation settings once and bootstrap authoritative worker copies."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ._configuration_context import (
    ConfigurationContext,
    current_configuration,
    install_configuration,
    restore_configuration,
)
from ._configuration_run import ConfigurationInvocation, accept_configuration_transfer
from ._configuration_snapshot import ConfigurationSnapshotError
from ._server_selection import normalize_servers
from .configuration import ConfigError, load_config
from .types import HTTPServer, StdioServer


def _selected_file(value: str | None) -> str | None:
    return None if value is None else str(Path(value).expanduser().absolute())


def _direct_servers(config: Any) -> tuple[HTTPServer | StdioServer, ...] | None:
    raw = config.getoption("--m3-server-selections")
    if raw is None:
        return None
    try:
        return normalize_servers(json.loads(raw))
    except (ValueError, TypeError, RecursionError):
        raise ConfigurationSnapshotError("server selection is invalid") from None


def _redact_server_arguments(arguments: list[str]) -> list[str]:
    result: list[str] = []
    iterator = iter(arguments)
    for argument in iterator:
        if argument == "--":
            result.append(argument)
            result.extend(iterator)
            break
        if argument in {"--m3-server-selections", "--m3-config"}:
            next(iterator, None)
        elif not argument.startswith(("--m3-server-selections=", "--m3-config=")):
            result.append(argument)
    return result


class InvocationConfigurationPlugin:
    def __init__(
        self, context: ConfigurationContext, *, owns_transferred_registry: bool = False
    ) -> None:
        self._closed = False
        self._owns_transferred_registry = owns_transferred_registry
        self._invocation = ConfigurationInvocation(
            context.config,
            literal_registry=context.literal_registry,
            selected_file=context.selected_file,
            server_selections=context.server_selections,
        )
        self.context = self._invocation.context
        self._token = install_configuration(self.context)

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: Any) -> None:
        spec = node.gateway.spec
        if self._invocation.snapshot.handles and (
            not spec.popen or spec.ssh or spec.socket or spec.via
        ):
            raise pytest.UsageError("private m3 configuration requires local workers")
        try:
            transfer = self._invocation.issue()
        except ConfigurationSnapshotError:
            raise pytest.UsageError(
                "m3 worker configuration bootstrap failed"
            ) from None
        node.workerinput["m3_configuration"] = transfer.model_dump_json()
        node.workerinput["mainargv"] = _redact_server_arguments(
            node.workerinput["mainargv"]
        )

    def close(self) -> None:
        if self._closed:
            return
        restore_configuration(self._token)
        self._invocation.close()
        if (
            self._owns_transferred_registry
            and self.context.literal_registry is not None
        ):
            self.context.literal_registry.close()
        self._closed = True


def configure_invocation(config: Any) -> InvocationConfigurationPlugin:
    requested_file = _selected_file(config.getoption("--m3-config"))
    workerinput = getattr(config, "workerinput", None)
    transferred = isinstance(workerinput, dict)
    context: ConfigurationContext | None = None
    plugin: InvocationConfigurationPlugin | None = None
    complete = False
    try:
        if isinstance(workerinput, dict):
            raw = workerinput.get("m3_configuration")
            if not isinstance(raw, str):
                raise ConfigurationSnapshotError("worker configuration is missing")
            context = accept_configuration_transfer(raw)
        else:
            inherited = current_configuration()
            context = (
                inherited
                if inherited is not None
                else ConfigurationContext(
                    load_config(
                        cwd=config.getoption("--project-root")
                        or getattr(config, "rootpath", Path.cwd()),
                        config_file=requested_file,
                    ),
                    selected_file=requested_file,
                    server_selections=_direct_servers(config),
                )
            )
        if requested_file is not None and requested_file != context.selected_file:
            raise ConfigurationSnapshotError(
                "worker cannot change invocation configuration"
            )
        plugin = InvocationConfigurationPlugin(
            context, owns_transferred_registry=transferred
        )
        if context.server_selections is not None:
            config.option.m3_server_selections = None
        # xdist sends invocation_params.args separately from workerinput.
        # These inputs have been consumed into the private snapshot; omit both
        # copies so workers cannot reread the source or receive its literals.
        config.invocation_params = replace(
            config.invocation_params,
            args=tuple(_redact_server_arguments(list(config.invocation_params.args))),
        )
        config.pluginmanager.register(plugin, "m3-configuration")
        config.add_cleanup(plugin.close)
        complete = True
        return plugin
    except (ConfigError, ConfigurationSnapshotError) as error:
        raise pytest.UsageError(f"m3 configuration: {error}") from None
    finally:
        if not complete:
            if plugin is not None:
                plugin.close()
            elif (
                transferred
                and context is not None
                and context.literal_registry is not None
            ):
                context.literal_registry.close()
