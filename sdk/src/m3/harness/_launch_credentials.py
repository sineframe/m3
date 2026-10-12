"""Private immutable credential inputs for one native session launch."""

from __future__ import annotations

import base64
import math
import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from .._literal_store import LiteralHandle, RunLiteralStore
from ..server_group import HarnessServerConfig
from ..trace.redaction import RedactionConfig
from ..types import SecretReference

if TYPE_CHECKING:
    from ..types import HarnessSpec
    from .contracts import HarnessLaunch


class CredentialResolutionError(RuntimeError):
    """A value-free failure before protected handoff or native spawn."""


@dataclass(frozen=True, slots=True, repr=False)
class NativeCredentialMaterial:
    login_bytes: bytes | None = None
    literals: frozenset[str] = frozenset()
    registry_available: bool = True

    def __repr__(self) -> str:
        return "NativeCredentialMaterial(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("native credential material cannot be serialized")


@dataclass(frozen=True, slots=True)
class _ServerHandles:
    command: tuple[LiteralHandle, ...] | None
    cwd: LiteralHandle | None
    endpoint: LiteralHandle | None
    environment: Mapping[str, LiteralHandle]
    headers: Mapping[str, LiteralHandle]


@dataclass(frozen=True, slots=True)
class _ExpectedValue:
    literal: LiteralHandle | None = None
    reference: SecretReference | None = None
    source_handle: LiteralHandle | None = None


@dataclass(frozen=True, slots=True)
class _ExpectedServer:
    key: str
    connection_id: str
    transport: str
    required: bool
    available: bool
    command: _ExpectedValue
    args: tuple[_ExpectedValue, ...]
    cwd: _ExpectedValue
    endpoint: _ExpectedValue
    environment: Mapping[str, _ExpectedValue]
    headers: Mapping[str, _ExpectedValue]
    tools: tuple[str, ...]
    loopback_only: bool


class LaunchCredentialBundle:
    """A session-owned handle registry; public specs retain their references.

    Collection belongs after effective capability validation and admission,
    immediately before materializing private launch files. Accessors never
    consult environment variables or credential providers again.
    """

    def __init__(
        self,
        configurations: Sequence[HarnessServerConfig],
        *,
        credential_references: Mapping[str, SecretReference],
        resolver_environment: Mapping[str, str] | None = None,
        child_environment: Mapping[str, str] | None = None,
        literal_registry: RunLiteralStore | None = None,
        additional_literals: Sequence[str | LiteralHandle] = (),
        selected_harness: HarnessSpec | None = None,
        captured_credentials: LaunchCredentialBundle | None = None,
        native_secret_inputs: Sequence[str | LiteralHandle] = (),
        stdio_baseline: Mapping[str, str] | None = None,
        login_bytes: bytes | None = None,
        trimmed_credential_targets: frozenset[str] = frozenset(),
        login_registry_available: bool = True,
    ) -> None:
        self._store = RunLiteralStore("launch-" + uuid4().hex)
        self._registered: set[LiteralHandle] = set()
        self._servers: dict[str, _ServerHandles] = {}
        self._credentials: Mapping[str, LiteralHandle] = MappingProxyType({})
        self._references: Mapping[str, LiteralHandle] = MappingProxyType({})
        self._effective_inputs: tuple[_ExpectedServer, ...] | None = None
        self._child_environment: Mapping[str, LiteralHandle] = MappingProxyType({})
        self._stdio_baseline: Mapping[str, LiteralHandle] = MappingProxyType({})
        self._login_bytes: LiteralHandle | None = None
        self._login_registry_available = login_registry_available
        self._selected_harness = selected_harness
        self._captured_credentials = captured_credentials
        self._native_selection: tuple[_ExpectedValue, ...] = ()
        # Snapshot once, before resolving any reference. Explicit values win
        # even when empty; an empty selected credential fails rather than
        # silently substituting an unrelated ambient credential.
        source = dict(os.environ)
        if resolver_environment is not None:
            source.update(resolver_environment)
        if captured_credentials is not None:
            captured_credentials.validate_inputs(configurations)
            source.update(captured_credentials.reference_environment())
        references: dict[str, LiteralHandle] = {}

        def pin(value: str) -> LiteralHandle:
            if not isinstance(value, str) or "\x00" in value:
                raise CredentialResolutionError("native launch input is invalid")
            handle = self._store.freeze(value)
            if value:
                self._registered.add(handle)
            return handle

        def named(reference: SecretReference) -> LiteralHandle:
            if reference.source != "environment":
                raise CredentialResolutionError("credential provider is unsupported")
            if reference.name not in references:
                value = source.get(reference.name)
                if not isinstance(value, str) or not value:
                    raise CredentialResolutionError("named credential is unavailable")
                references[reference.name] = pin(value)
            return references[reference.name]

        def resolve(value: object, *, template: bool = True) -> LiteralHandle:
            if isinstance(value, SecretReference):
                return named(value)
            if isinstance(value, LiteralHandle):
                if literal_registry is None:
                    raise CredentialResolutionError("literal registry is unavailable")
                value = literal_registry.resolve(value)
            if not isinstance(value, str):
                raise CredentialResolutionError("native launch input is invalid")
            original = pin(value)
            rendered = value
            if template:
                pieces: list[str] = []
                offset = 0
                while True:
                    begin = value.find("${", offset)
                    if begin < 0:
                        pieces.append(value[offset:])
                        break
                    end = value.find("}", begin + 2)
                    if end < 0:
                        pieces.append(value[offset:])
                        break
                    name = value[begin + 2 : end]
                    if len(name) > 256 or not re.fullmatch(r"[A-Za-z0-9_]+", name):
                        raise CredentialResolutionError(
                            "credential template is invalid"
                        )
                    pieces.append(value[offset:begin])
                    pieces.append(
                        self._store.resolve(
                            named(SecretReference(source="environment", name=name))
                        )
                    )
                    pin(value[begin : end + 1])
                    offset = end + 1
                rendered = "".join(pieces)
            return original if rendered == value else pin(rendered)

        def fields(values: Mapping[str, object]) -> Mapping[str, LiteralHandle]:
            return MappingProxyType(
                {key: resolve(value) for key, value in values.items()}
            )

        try:
            self._source_inputs = self._snapshot_inputs(configurations)
            self._native_selection = tuple(
                _ExpectedValue(literal=self._store.freeze(value))
                if isinstance(value, str)
                else _ExpectedValue(source_handle=value)
                for value in native_secret_inputs
            )
            if login_bytes is not None:
                self._login_bytes = self._store.freeze(
                    base64.b64encode(login_bytes).decode("ascii")
                )
            if captured_credentials is not None:
                references.update(
                    {
                        name: pin(value)
                        for name, value in captured_credentials.reference_environment().items()
                    }
                )
                for captured_literal in captured_credentials.redaction().secrets:
                    pin(captured_literal)
                self._stdio_baseline = MappingProxyType(
                    {
                        key: self._store.freeze(value)
                        for key, value in captured_credentials._values(
                            captured_credentials._stdio_baseline
                        ).items()
                    }
                )
            for value in (*native_secret_inputs, *additional_literals):
                resolve(value, template=False)
            self._credentials = MappingProxyType(
                {target: named(ref) for target, ref in credential_references.items()}
            )
            for target in trimmed_credential_targets.intersection(self._credentials):
                pin(self._store.resolve(self._credentials[target]).strip())
            self._child_environment = MappingProxyType(
                {key: pin(value) for key, value in (child_environment or {}).items()}
            )
            if captured_credentials is None:
                self._stdio_baseline = MappingProxyType(
                    {
                        key: self._store.freeze(value)
                        for key, value in (stdio_baseline or {}).items()
                    }
                )
            for configuration in configurations:
                if not configuration.available:
                    continue
                if configuration.connection_id in self._servers:
                    raise CredentialResolutionError("duplicate launch server identity")
                if captured_credentials is not None:
                    key = configuration.connection_id
                    captured = captured_credentials._servers[key]

                    def copy_handle(
                        handle: LiteralHandle | None,
                    ) -> LiteralHandle | None:
                        return (
                            pin(captured_credentials._store.resolve(handle))
                            if handle is not None
                            else None
                        )

                    self._servers[key] = _ServerHandles(
                        command=tuple(
                            pin(captured_credentials._store.resolve(handle))
                            for handle in captured.command
                        )
                        if captured.command is not None
                        else None,
                        cwd=copy_handle(captured.cwd),
                        endpoint=copy_handle(captured.endpoint),
                        environment=MappingProxyType(
                            {
                                name: pin(value)
                                for name, value in captured_credentials.server_environment(
                                    key
                                ).items()
                            }
                        ),
                        headers=MappingProxyType(
                            {
                                name: pin(value)
                                for name, value in captured_credentials.server_headers(
                                    key
                                ).items()
                            }
                        ),
                    )
                    continue
                command = (
                    tuple(
                        resolve(part, template=False)
                        for part in (configuration.command, *configuration.args)
                    )
                    if configuration.command is not None
                    else None
                )
                self._servers[configuration.connection_id] = _ServerHandles(
                    command=command,
                    cwd=resolve(configuration.cwd, template=False)
                    if configuration.cwd is not None
                    else None,
                    endpoint=resolve(configuration.endpoint)
                    if configuration.endpoint is not None
                    else None,
                    environment=fields(configuration.environment),
                    headers=fields(configuration.headers),
                )
            self._references = MappingProxyType(references)
        except BaseException:
            self.close()
            raise

    def __repr__(self) -> str:
        return "LaunchCredentialBundle(<private>)"

    def __getstate__(self) -> None:
        raise TypeError("launch credentials cannot be serialized")

    def _snapshot_inputs(
        self, configurations: Sequence[HarnessServerConfig]
    ) -> tuple[_ExpectedServer, ...]:
        def snapshot(value: object) -> _ExpectedValue:
            if value is None:
                return _ExpectedValue()
            if isinstance(value, str):
                return _ExpectedValue(literal=self._store.freeze(value))
            if isinstance(value, SecretReference):
                return _ExpectedValue(reference=value)
            if isinstance(value, LiteralHandle):
                return _ExpectedValue(source_handle=value)
            raise CredentialResolutionError("native launch input is invalid")

        return tuple(
            _ExpectedServer(
                key=configuration.key,
                connection_id=configuration.connection_id,
                transport=configuration.transport.value,
                required=configuration.required,
                available=configuration.available,
                command=snapshot(configuration.command),
                args=tuple(snapshot(value) for value in configuration.args),
                cwd=snapshot(configuration.cwd),
                endpoint=snapshot(configuration.endpoint),
                environment=MappingProxyType(
                    {
                        key: snapshot(value)
                        for key, value in configuration.environment.items()
                    }
                ),
                headers=MappingProxyType(
                    {
                        key: snapshot(value)
                        for key, value in configuration.headers.items()
                    }
                ),
                tools=configuration.tools,
                loopback_only=configuration.loopback_only,
            )
            for configuration in configurations
        )

    def bind_effective_inputs(
        self, configurations: Sequence[HarnessServerConfig]
    ) -> None:
        """Seal the successful capture rewrite before native configuration writes."""

        if self._effective_inputs is not None:
            self.validate_inputs(configurations, effective=True)
            return
        self._effective_inputs = self._snapshot_inputs(configurations)

    @property
    def login_registry_available(self) -> bool:
        return self._login_registry_available

    def adopts(self, captured: LaunchCredentialBundle) -> bool:
        return self._captured_credentials is captured

    def validate_selection(self, launch: HarnessLaunch) -> None:
        if self._selected_harness != launch.spec.harness:
            raise CredentialResolutionError("pinned harness inputs changed")
        current = launch._native_secret_inputs
        if len(current) != len(self._native_selection) or not all(
            (isinstance(value, str) and value == self._store.resolve(expected.literal))
            if expected.literal is not None
            else value == expected.source_handle
            for expected, value in zip(self._native_selection, current, strict=True)
        ):
            raise CredentialResolutionError("pinned native settings changed")

    @property
    def effective_inputs_bound(self) -> bool:
        return self._effective_inputs is not None

    def validate_inputs(
        self, configurations: Sequence[HarnessServerConfig], *, effective: bool = False
    ) -> None:
        expected = self._effective_inputs if effective else self._source_inputs
        if expected is None or len(expected) != len(configurations):
            raise CredentialResolutionError("pinned launch inputs changed")

        def matches(original: _ExpectedValue, value: object) -> bool:
            if original.literal is not None:
                return isinstance(value, str) and value == self._store.resolve(
                    original.literal
                )
            if original.reference is not None:
                return value == original.reference
            if original.source_handle is not None:
                return value == original.source_handle
            return value is None

        def mapping_matches(
            original: Mapping[str, _ExpectedValue], values: Mapping[str, object]
        ) -> bool:
            return original.keys() == values.keys() and all(
                matches(item, values[key]) for key, item in original.items()
            )

        for original, configuration in zip(expected, configurations, strict=True):
            valid = (
                original.key == configuration.key
                and original.connection_id == configuration.connection_id
                and original.transport == configuration.transport.value
                and original.required == configuration.required
                and original.available == configuration.available
                and matches(original.command, configuration.command)
                and len(original.args) == len(configuration.args)
                and all(
                    matches(left, right)
                    for left, right in zip(
                        original.args, configuration.args, strict=True
                    )
                )
                and matches(original.cwd, configuration.cwd)
                and matches(original.endpoint, configuration.endpoint)
                and mapping_matches(original.environment, configuration.environment)
                and mapping_matches(original.headers, configuration.headers)
                and original.tools == configuration.tools
                and original.loopback_only == configuration.loopback_only
            )
            if not valid:
                raise CredentialResolutionError("pinned launch inputs changed")

    def _values(self, handles: Mapping[str, LiteralHandle]) -> dict[str, str]:
        return {name: self._store.resolve(handle) for name, handle in handles.items()}

    def credential_environment(self) -> dict[str, str]:
        return self._values(self._credentials)

    def reference_environment(self) -> dict[str, str]:
        return self._values(self._references)

    def child_environment(self) -> dict[str, str]:
        return self._values(self._child_environment)

    def login_bytes(self) -> bytes | None:
        handle = self._login_bytes
        return (
            base64.b64decode(self._store.resolve(handle))
            if handle is not None
            else None
        )

    def server_environment(self, connection_id: str) -> dict[str, str]:
        return self._values(self._servers[connection_id].environment)

    def stdio_environment(self, connection_id: str) -> dict[str, str]:
        return {
            **self._values(self._stdio_baseline),
            **self.server_environment(connection_id),
        }

    def server_headers(self, connection_id: str) -> dict[str, str]:
        return self._values(self._servers[connection_id].headers)

    def server_endpoint(self, connection_id: str) -> str | None:
        handle = self._servers[connection_id].endpoint
        return self._store.resolve(handle) if handle is not None else None

    def server_command(self, connection_id: str) -> tuple[str, ...] | None:
        handles = self._servers[connection_id].command
        return (
            tuple(self._store.resolve(handle) for handle in handles)
            if handles
            else None
        )

    def server_cwd(self, connection_id: str) -> str | None:
        handle = self._servers[connection_id].cwd
        return self._store.resolve(handle) if handle is not None else None

    def redaction(self) -> RedactionConfig:
        return RedactionConfig(
            secrets=frozenset(
                self._store.resolve(handle) for handle in self._registered
            ),
            include_environment=False,
        )

    def close(self) -> None:
        self._store.close()
        self._servers.clear()
        self._registered.clear()
        self._credentials = MappingProxyType({})
        self._references = MappingProxyType({})
        self._child_environment = MappingProxyType({})
        self._stdio_baseline = MappingProxyType({})
        self._login_bytes = None
        self._source_inputs = ()
        self._effective_inputs = None
        self._selected_harness = None
        self._captured_credentials = None
        self._native_selection = ()


def check_credential_deadline(launch: HarnessLaunch) -> None:
    from .contracts import HarnessStartupError

    deadline = launch._credential_deadline
    if deadline is not None and (
        not math.isfinite(deadline) or deadline <= time.monotonic()
    ):
        raise HarnessStartupError("native credential preparation deadline exceeded")


async def activate_native_launch(
    launch: HarnessLaunch,
    *,
    resolver_environment: Mapping[str, str] | None,
    child_environment: Mapping[str, str],
) -> HarnessLaunch:
    """Pin inputs after validation and activate private transport handoffs."""

    from ..transport.capture_proxy import McpCaptureManager
    from .contracts import HarnessStartupError

    check_credential_deadline(launch)
    if launch._credentials is not None:
        try:
            launch._credentials.validate_selection(launch)
            launch._credentials.validate_inputs(
                launch.configurations,
                effective=launch._credentials.effective_inputs_bound,
            )
        except CredentialResolutionError as error:
            raise HarnessStartupError(str(error)) from None
        if launch._credentials.effective_inputs_bound:
            if isinstance(launch.capture, McpCaptureManager):
                await launch.capture.activate(launch._credentials)
            check_credential_deadline(launch)
            return launch
    capture = launch.capture
    bundle = launch._credentials or collect_native_credentials(
        launch,
        resolver_environment=resolver_environment,
        child_environment=child_environment,
    )
    check_credential_deadline(launch)
    effective = replace(launch, _credentials=bundle)
    if isinstance(capture, McpCaptureManager):
        configurations = await capture.activate(bundle)
        effective = replace(effective, configurations=configurations)
    check_credential_deadline(launch)
    bundle.bind_effective_inputs(effective.configurations)
    return effective


def collect_native_credentials(
    launch: HarnessLaunch,
    *,
    resolver_environment: Mapping[str, str] | None,
    child_environment: Mapping[str, str],
    material: NativeCredentialMaterial | None = None,
) -> LaunchCredentialBundle:
    from mcp.client.stdio import get_default_environment

    from ..transport.capture_proxy import McpCaptureManager
    from ..types import Codex
    from .codex import _CODEX_API_KEY_TARGETS
    from .contracts import HarnessStartupError

    check_credential_deadline(launch)
    if (
        isinstance(launch.capture, McpCaptureManager)
        and launch.capture.adoptable_credentials() is not None
        and launch._credential_deadline is not None
    ):
        raise HarnessStartupError(
            "bounded native launch requires deferred capture preparation"
        )
    material = material or NativeCredentialMaterial()
    configurations = (
        launch.capture.credential_configurations()
        if isinstance(launch.capture, McpCaptureManager)
        else launch.configurations
    )
    try:
        bundle = LaunchCredentialBundle(
            configurations,
            credential_references=getattr(
                launch.spec.harness, "credential_references", {}
            ),
            resolver_environment=resolver_environment,
            child_environment=child_environment,
            literal_registry=launch._literal_registry,
            stdio_baseline=get_default_environment(),
            trimmed_credential_targets=frozenset(_CODEX_API_KEY_TARGETS)
            if isinstance(launch.spec.harness, Codex)
            else frozenset(),
            login_bytes=material.login_bytes,
            additional_literals=tuple(material.literals),
            native_secret_inputs=launch._native_secret_inputs,
            selected_harness=launch.spec.harness,
            captured_credentials=launch.capture.adoptable_credentials()
            if isinstance(launch.capture, McpCaptureManager)
            else None,
            login_registry_available=material.registry_available,
        )
        if bundle._captured_credentials is not None:
            bundle.bind_effective_inputs(launch.configurations)
        try:
            check_credential_deadline(launch)
        except HarnessStartupError:
            bundle.close()
            raise
        return bundle
    except CredentialResolutionError as error:
        raise HarnessStartupError(str(error)) from None


def release_native_credentials(launch: HarnessLaunch | None) -> None:
    """Drop session memory after native cleanup; capture releases its own owner later."""

    from ..transport.capture_proxy import McpCaptureManager

    if launch is None or launch._credentials is None:
        return
    if isinstance(
        launch.capture, McpCaptureManager
    ) and launch.capture.owns_credential_bundle(launch._credentials):
        return
    launch._credentials.close()
