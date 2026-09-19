"""Immutable definitions, expansion, and execution helpers for test matrices.

Matrix construction and expansion are pure operations.  Execution, including
persistence and subprocess work, starts only when a matrix case helper is
called.  Pytest integration remains an optional boundary around ``cases()``.
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import keyword as _keyword
from collections.abc import Iterable as _Iterable
from collections.abc import Mapping as _Mapping
from contextlib import (
    AbstractAsyncContextManager as _AsyncContextManager,
)
from contextlib import (
    AbstractContextManager as _ContextManager,
)
from math import isfinite as _isfinite
from typing import (
    TYPE_CHECKING as _TYPE_CHECKING,
)
from typing import (
    Any as _Any,
)
from typing import (
    Literal as _Literal,
)

from pydantic import Field as _Field
from pydantic import model_validator as _model_validator

from .errors import UnsupportedFeature as _UnsupportedFeature
from .types import (
    ACPAgent as _ACPAgent,
)
from .types import (
    AgentSpec as _AgentSpec,
)
from .types import (
    CallTool as _CallTool,
)
from .types import (
    ClaudeCode as _ClaudeCode,
)
from .types import (
    Codex as _Codex,
)
from .types import (
    DirectSpec as _DirectSpec,
)
from .types import (
    ExecutionResult as _ExecutionResult,
)
from .types import (
    FrozenModel as _FrozenModel,
)
from .types import (
    InProcessServer as _InProcessServer,
)
from .types import (
    NativeToolPolicy as _NativeToolPolicy,
)
from .types import (
    OpenCode as _OpenCode,
)
from .types import (
    Pi as _Pi,
)
from .types import (
    RestrictiveToolPolicy as _RestrictiveToolPolicy,
)
from .types import (
    ServerBinding as _ServerBinding,
)
from .types import (
    ServerValue as _ServerValue,
)
from .types import (
    TextContent as _TextContent,
)
from .types import (
    ToolPolicy as _ToolPolicy,
)
from .types import (
    UserMessage as _UserMessage,
)

if _TYPE_CHECKING:
    import pytest as _pytest

    from .agent_session import AsyncAgentSession as _AsyncAgentSession
    from .async_api import AsyncMCPTestKit as _AsyncMCPTestKit
    from .sync_api import AgentSession as _AgentSession
    from .sync_api import MCPTestKit as _MCPTestKit


_Scalar = str | int | float | bool | None
_MATRIX_METADATA_PREFIX = "m3.matrix."


def _merge_metadata(
    generated: dict[str, _Scalar],
    user: _Mapping[str, _Scalar] | None,
) -> dict[str, _Scalar]:
    if user is None:
        return generated
    merged = dict(generated)
    for key, value in user.items():
        if not isinstance(key, str):
            raise ValueError("matrix metadata keys must be strings")
        if key.startswith(_MATRIX_METADATA_PREFIX):
            raise ValueError(f"matrix metadata key {key!r} is reserved")
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            raise ValueError(f"matrix metadata value for {key!r} must be scalar")
        if isinstance(value, float) and not _isfinite(value):
            raise ValueError(f"matrix metadata value for {key!r} must be finite")
        merged[key] = value
    return merged


def _validate_parametrize_argname(argname: str) -> None:
    if (
        not isinstance(argname, str)
        or not argname
        or not argname.isidentifier()
        or _keyword.iskeyword(argname)
    ):
        raise ValueError("argname must be one non-empty Python identifier")


def _pytest_parametrize(
    argname: str,
    cases: tuple[_Any, ...],
) -> _pytest.MarkDecorator:
    _validate_parametrize_argname(argname)
    try:
        import pytest as _pytest_runtime
    except ModuleNotFoundError as exc:
        if exc.name != "pytest":
            raise
        raise ImportError(
            "matrix.parametrize() requires the optional pytest dependency; "
            "install it with m3[pytest]"
        ) from exc
    return _pytest_runtime.mark.parametrize(
        argname,
        cases,
        ids=tuple(case.id for case in cases),
    )


class ToolCase(_FrozenModel):
    """One deterministic call definition owned by a server case."""

    name: str = _Field(min_length=1, max_length=256)
    id: str | None = _Field(default=None, min_length=1, max_length=256)
    arguments: _Mapping[str, _Any] = _Field(default_factory=dict)
    prompt: str | _UserMessage | None = None

    @_model_validator(mode="after")
    def _default_logical_id(self) -> ToolCase:
        if self.id is None:
            object.__setattr__(self, "id", self.name)
        return self


class ServerCase(_FrozenModel):
    """A serializable MCP server and its owned logical tool cases."""

    name: str = _Field(min_length=1, max_length=256)
    server: _ServerValue
    tools: tuple[ToolCase, ...]

    @_model_validator(mode="after")
    def _validate_tools_and_server(self) -> ServerCase:
        if isinstance(self.server, _InProcessServer):
            raise ValueError(
                "ServerCase does not support InProcessServer; use a serializable server"
            )
        if not self.tools:
            raise ValueError(f"ServerCase {self.name!r} must define at least one tool")
        seen: dict[str, int] = {}
        for index, tool in enumerate(self.tools):
            logical_id = _tool_id(tool)
            previous = seen.get(logical_id)
            if previous is not None:
                raise ValueError(
                    f"ServerCase {self.name!r} has duplicate tool id {logical_id!r} "
                    f"at indexes {previous} and {index}"
                )
            seen[logical_id] = index
        return self

    def tool(self, logical_id: str) -> ToolCase:
        """Return the tool with ``logical_id`` or raise a clear lookup error."""

        for tool in self.tools:
            if _tool_id(tool) == logical_id:
                return tool
        raise KeyError(f"tool {logical_id!r} was not found on server {self.name!r}")


class HarnessCase(_FrozenModel):
    """One named harness configuration in a harness matrix."""

    name: str = _Field(min_length=1, max_length=256)
    harness: _ClaudeCode | _OpenCode | _Codex | _Pi | _ACPAgent


class ToolMatrixCase(_FrozenModel):
    """One server-owned deterministic tool cell."""

    id: str = _Field(min_length=1, max_length=1024)
    server: ServerCase
    tool: ToolCase
    matrix_id: str | None = None
    cell_id: str | None = None
    trial: int = _Field(default=1, strict=True, ge=1)
    trial_count: int = _Field(default=1, strict=True, ge=1)

    def _metadata(
        self,
        metadata: _Mapping[str, _Scalar] | None,
    ) -> dict[str, _Scalar]:
        values: dict[str, _Scalar] = {
            "m3.matrix.case_id": self.id,
            "m3.matrix.kind": "tool",
            "m3.matrix.mode": "tool",
            "m3.matrix.servers": self.server.name,
            "m3.matrix.tool": self.tool.name,
            "m3.matrix.matrix_id": self.matrix_id,
            "m3.matrix.cell_id": self.cell_id or self.id,
            "m3.matrix.trial": self.trial,
            "m3.matrix.trial_count": self.trial_count,
        }
        return _merge_metadata(values, metadata)

    def _spec(
        self,
        *,
        timeout: float | None,
        validate_schemas: bool,
        metadata: _Mapping[str, _Scalar] | None,
    ) -> _DirectSpec:
        alias = self.server.name
        return _DirectSpec(
            case_id=f"{self.matrix_id}:{self.cell_id or self.id}",
            servers=(_ServerBinding(server=self.server.server, alias=alias),),
            operation=_CallTool(
                server=alias,
                name=self.tool.name,
                arguments=self.tool.arguments,
            ),
            timeout_seconds=timeout,
            validate_schemas=validate_schemas,
            metadata=self._metadata(metadata),
        )

    def run(
        self,
        *,
        kit: _MCPTestKit | None = None,
        timeout: float | None = None,
        validate_schemas: bool = False,
        metadata: _Mapping[str, _Scalar] | None = None,
    ) -> _ExecutionResult:
        """Run this cell through the normal synchronous execution boundary."""

        spec = self._spec(
            timeout=timeout,
            validate_schemas=validate_schemas,
            metadata=metadata,
        )
        owned = kit is None
        selected = kit
        if selected is None:
            from .sync_api import MCPTestKit as _MCPTestKitRuntime

            selected = _MCPTestKitRuntime()
        try:
            return selected.run(spec)
        finally:
            if owned:
                selected.close()

    async def run_async(
        self,
        *,
        kit: _AsyncMCPTestKit | None = None,
        timeout: float | None = None,
        validate_schemas: bool = False,
        metadata: _Mapping[str, _Scalar] | None = None,
    ) -> _ExecutionResult:
        """Run this cell through the normal asynchronous execution boundary."""

        spec = self._spec(
            timeout=timeout,
            validate_schemas=validate_schemas,
            metadata=metadata,
        )
        owned = kit is None
        selected = kit
        if selected is None:
            from .async_api import AsyncMCPTestKit as _AsyncMCPTestKitRuntime

            selected = _AsyncMCPTestKitRuntime()
        try:
            return await selected.run(spec)
        finally:
            if owned:
                await selected.aclose()


class HarnessMatrixCase(_FrozenModel):
    """One harness matrix cell, optionally scoped to a server and tool."""

    id: str = _Field(min_length=1, max_length=1024)
    mode: _Literal["each_server", "all_servers", "each_tool"]
    harness: HarnessCase
    servers: tuple[ServerCase, ...]
    trial: int = _Field(strict=True, ge=1)
    matrix_id: str | None = None
    cell_id: str | None = None
    trial_count: int = _Field(default=1, strict=True, ge=1)
    selected_tool_id: str | None = _Field(default=None, min_length=1, max_length=256)

    @_model_validator(mode="after")
    def _validate_scope(self) -> HarnessMatrixCase:
        if not self.servers:
            raise ValueError("harness matrix case requires at least one server")
        if self.mode in {"each_server", "each_tool"} and len(self.servers) != 1:
            raise ValueError(
                f"{self.mode} harness matrix cases require exactly one server"
            )
        if self.mode == "each_tool":
            if self.selected_tool_id is None:
                raise ValueError(
                    "each_tool harness matrix cases require selected_tool_id"
                )
            try:
                self.servers[0].tool(self.selected_tool_id)
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
        elif self.selected_tool_id is not None:
            raise ValueError(
                f"selected_tool_id is only valid for each_tool cases, not {self.mode}"
            )
        return self

    @property
    def server(self) -> ServerCase:
        if self.mode not in {"each_server", "each_tool"}:
            raise ValueError(
                "server is only available for each_server and each_tool matrix cases"
            )
        return self.servers[0]

    @property
    def tool(self) -> ToolCase:
        if self.mode != "each_tool" or self.selected_tool_id is None:
            raise ValueError("tool is only available for each_tool matrix cases")
        return self.server.tool(self.selected_tool_id)

    @classmethod
    def _build(
        cls,
        *,
        id: str,
        mode: _Literal["each_server", "all_servers", "each_tool"],
        harness: HarnessCase,
        servers: tuple[ServerCase, ...],
        trial: int,
        tool: ToolCase | None = None,
        matrix_id: str | None = None,
        cell_id: str | None = None,
        trial_count: int = 1,
    ) -> HarnessMatrixCase:
        return cls(
            id=id,
            mode=mode,
            harness=harness,
            servers=servers,
            trial=trial,
            selected_tool_id=_tool_id(tool) if tool is not None else None,
            matrix_id=matrix_id,
            cell_id=cell_id,
            trial_count=trial_count,
        )

    def _metadata(
        self,
        metadata: _Mapping[str, _Scalar] | None,
    ) -> dict[str, _Scalar]:
        values: dict[str, _Scalar] = {
            "m3.matrix.case_id": self.id,
            "m3.matrix.kind": "harness",
            "m3.matrix.mode": self.mode,
            "m3.matrix.servers": ",".join(server.name for server in self.servers),
            "m3.matrix.harness": self.harness.name,
            "m3.matrix.trial": self.trial,
            "m3.matrix.matrix_id": self.matrix_id,
            "m3.matrix.cell_id": self.cell_id or self.id,
            "m3.matrix.trial_count": self.trial_count,
        }
        if self.mode == "each_tool":
            values["m3.matrix.tool"] = self.tool.name
        return _merge_metadata(values, metadata)

    def _selected_tools(self) -> tuple[tuple[str, ToolCase], ...]:
        if self.mode == "each_tool":
            server = self.server
            return ((server.name, self.tool),)
        return tuple(
            (server.name, tool) for server in self.servers for tool in server.tools
        )

    def _policy(self, explicit: _ToolPolicy | None) -> _ToolPolicy:
        if explicit is not None:
            return explicit
        if isinstance(self.harness.harness, _ClaudeCode):
            server = self.servers[0]
            return _NativeToolPolicy(
                harness="claude-code",
                policy={"mode": "mcp_only", "server": server.name},
                nonportable_reason=(
                    "Claude Code enforces MCP access at server scope; exact tool-level "
                    "restriction is not portable"
                ),
            )
        qualified = tuple(
            f"{server_name}:{tool.name}" for server_name, tool in self._selected_tools()
        )
        return _RestrictiveToolPolicy(allowed_tools=qualified)

    def _bindings(self) -> tuple[_ServerBinding, ...]:
        return tuple(
            _ServerBinding(server=server.server, alias=server.name)
            for server in self.servers
        )

    def _message(self, message: str | _UserMessage | None) -> _UserMessage:
        if message is not None:
            return (
                _UserMessage(content=(_TextContent(text=message),))
                if isinstance(message, str)
                else message
            )
        if self.mode == "each_tool":
            prompt = self.tool.prompt
            if prompt is not None:
                return (
                    _UserMessage(content=(_TextContent(text=prompt),))
                    if isinstance(prompt, str)
                    else prompt
                )
        raise ValueError(f"matrix case {self.id!r} requires an explicit message")

    def _spec(
        self,
        message: str | _UserMessage | None,
        *,
        timeout: float | None,
        tool_policy: _ToolPolicy | None,
        metadata: _Mapping[str, _Scalar] | None,
    ) -> _AgentSpec:
        return _AgentSpec(
            case_id=f"{self.matrix_id}:{self.cell_id or self.id}",
            servers=self._bindings(),
            harness=self.harness.harness,
            message=self._message(message),
            timeout_seconds=timeout,
            tool_policy=self._policy(tool_policy),
            metadata=self._metadata(metadata),
        )

    def _session_spec(
        self,
        *,
        tool_policy: _ToolPolicy | None,
        metadata: _Mapping[str, _Scalar] | None,
    ) -> _AgentSpec:
        return _AgentSpec(
            case_id=f"{self.matrix_id}:{self.cell_id or self.id}",
            servers=self._bindings(),
            harness=self.harness.harness,
            message=None,
            tool_policy=self._policy(tool_policy),
            metadata=self._metadata(metadata),
        )

    def run(
        self,
        message: str | _UserMessage | None = None,
        *,
        kit: _MCPTestKit | None = None,
        timeout: float | None = None,
        tool_policy: _ToolPolicy | None = None,
        metadata: _Mapping[str, _Scalar] | None = None,
    ) -> _ExecutionResult:
        """Run one prompt through the normal synchronous execution boundary."""

        spec = self._spec(
            message,
            timeout=timeout,
            tool_policy=tool_policy,
            metadata=metadata,
        )
        owned = kit is None
        selected = kit
        if selected is None:
            from .sync_api import MCPTestKit as _MCPTestKitRuntime

            selected = _MCPTestKitRuntime()
        try:
            return selected.run(spec)
        finally:
            if owned:
                selected.close()

    async def run_async(
        self,
        message: str | _UserMessage | None = None,
        *,
        kit: _AsyncMCPTestKit | None = None,
        timeout: float | None = None,
        tool_policy: _ToolPolicy | None = None,
        metadata: _Mapping[str, _Scalar] | None = None,
    ) -> _ExecutionResult:
        """Run one prompt through the normal asynchronous execution boundary."""

        spec = self._spec(
            message,
            timeout=timeout,
            tool_policy=tool_policy,
            metadata=metadata,
        )
        owned = kit is None
        selected = kit
        if selected is None:
            from .async_api import AsyncMCPTestKit as _AsyncMCPTestKitRuntime

            selected = _AsyncMCPTestKitRuntime()
        try:
            return await selected.run(spec)
        finally:
            if owned:
                await selected.aclose()

    def session(
        self,
        *,
        kit: _MCPTestKit | None = None,
        tool_policy: _ToolPolicy | None = None,
        metadata: _Mapping[str, _Scalar] | None = None,
    ) -> _ContextManager[_AgentSession]:
        """Return a context manager for a continuing synchronous session."""

        return _MatrixSessionContext(
            self._session_spec(tool_policy=tool_policy, metadata=metadata), kit
        )

    def async_session(
        self,
        *,
        kit: _AsyncMCPTestKit | None = None,
        tool_policy: _ToolPolicy | None = None,
        metadata: _Mapping[str, _Scalar] | None = None,
    ) -> _AsyncContextManager[_AsyncAgentSession]:
        """Return an async context manager for a continuing session."""

        return _AsyncMatrixSessionContext(
            self._session_spec(tool_policy=tool_policy, metadata=metadata), kit
        )


def _validate_servers(servers: tuple[ServerCase, ...]) -> None:
    if not servers:
        raise ValueError("matrix requires at least one server")
    seen: dict[str, int] = {}
    for index, server in enumerate(servers):
        previous = seen.get(server.name)
        if previous is not None:
            raise ValueError(
                f"matrix has duplicate server name {server.name!r} at indexes {previous} and {index}"
            )
        seen[server.name] = index


def _tool_id(tool: ToolCase) -> str:
    assert tool.id is not None
    return tool.id


def _validate_harnesses(harnesses: tuple[HarnessCase, ...]) -> None:
    if not harnesses:
        raise ValueError("harness matrix requires at least one harness")
    seen: dict[str, int] = {}
    for index, harness in enumerate(harnesses):
        previous = seen.get(harness.name)
        if previous is not None:
            raise ValueError(
                f"harness matrix has duplicate harness name {harness.name!r} "
                f"at indexes {previous} and {index}"
            )
        seen[harness.name] = index


def _validate_trials(trials: int) -> None:
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError("trials must be an integer greater than or equal to 1")


def _ensure_unique_ids(ids: _Iterable[str]) -> None:
    seen: set[str] = set()
    for case_id in ids:
        if case_id in seen:
            raise ValueError(f"matrix generated duplicate case id {case_id!r}")
        seen.add(case_id)


def _derived_matrix_id(value: object) -> str:
    """Derive a stable identity from the logical matrix definition."""
    encoded = _json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return "matrix-" + _hashlib.sha256(encoded).hexdigest()[:24]


class ToolMatrix(_FrozenModel):
    """Expand server-owned deterministic tool cases in declared order."""

    servers: tuple[ServerCase, ...]
    id: str | None = _Field(default=None, min_length=1, max_length=256)
    matrix_id: str | None = _Field(default=None, min_length=1, max_length=256)
    trials: int = _Field(default=1, strict=True, ge=1)

    @_model_validator(mode="after")
    def _validate_matrix(self) -> ToolMatrix:
        _validate_servers(self.servers)
        _validate_trials(self.trials)
        if (
            self.id is not None
            and self.matrix_id is not None
            and self.id != self.matrix_id
        ):
            raise ValueError("matrix id and matrix_id must match")
        resolved_id = (
            self.matrix_id
            or self.id
            or _derived_matrix_id(
                self.model_dump(mode="json", exclude={"id", "matrix_id", "trials"})
            )
        )
        object.__setattr__(self, "matrix_id", resolved_id)
        object.__setattr__(self, "id", resolved_id)
        _ensure_unique_ids(
            f"{server.name}/{_tool_id(tool)}"
            for server in self.servers
            for tool in server.tools
        )
        return self

    def cases(self) -> tuple[ToolMatrixCase, ...]:
        return tuple(
            ToolMatrixCase(
                id=f"{server.name}/{_tool_id(tool)}"
                if self.trials == 1
                else f"{server.name}/{_tool_id(tool)}/trial-{trial}",
                server=server,
                tool=tool,
                matrix_id=self.matrix_id,
                cell_id=f"{server.name}/{_tool_id(tool)}",
                trial=trial,
                trial_count=self.trials,
            )
            for server in self.servers
            for tool in server.tools
            for trial in range(1, self.trials + 1)
        )

    def parametrize(self, argname: str = "case") -> _pytest.MarkDecorator:
        """Return pytest's ordinary parametrization decorator for this matrix."""

        return _pytest_parametrize(argname, self.cases())


class HarnessMatrix(_FrozenModel):
    """Expand harness executions over servers, tools, and repeated trials."""

    mode: _Literal["each_server", "all_servers", "each_tool"]
    servers: tuple[ServerCase, ...]
    harnesses: tuple[HarnessCase, ...]
    trials: int = _Field(strict=True, ge=1)
    id: str | None = _Field(default=None, min_length=1, max_length=256)
    matrix_id: str | None = _Field(default=None, min_length=1, max_length=256)

    @_model_validator(mode="after")
    def _validate_matrix(self) -> HarnessMatrix:
        _validate_servers(self.servers)
        _validate_harnesses(self.harnesses)
        _validate_trials(self.trials)
        if (
            self.id is not None
            and self.matrix_id is not None
            and self.id != self.matrix_id
        ):
            raise ValueError("matrix id and matrix_id must match")
        resolved_id = (
            self.matrix_id
            or self.id
            or _derived_matrix_id(
                self.model_dump(mode="json", exclude={"id", "matrix_id", "trials"})
            )
        )
        object.__setattr__(self, "matrix_id", resolved_id)
        object.__setattr__(self, "id", resolved_id)
        if self.mode == "all_servers" and any(
            isinstance(case.harness, _ClaudeCode) for case in self.harnesses
        ):
            if len(self.servers) > 1:
                raise _UnsupportedFeature(
                    "HarnessMatrix.all_servers does not support ClaudeCode with multiple servers"
                )
        if self.mode == "each_server":
            ids = (
                self._case_id(server.name, harness.name, trial)
                for server in self.servers
                for harness in self.harnesses
                for trial in range(1, self.trials + 1)
            )
        elif self.mode == "all_servers":
            ids = (
                self._case_id("all-servers", harness.name, trial)
                for harness in self.harnesses
                for trial in range(1, self.trials + 1)
            )
        else:
            ids = (
                self._case_id(f"{server.name}/{_tool_id(tool)}", harness.name, trial)
                for server in self.servers
                for tool in server.tools
                for harness in self.harnesses
                for trial in range(1, self.trials + 1)
            )
        _ensure_unique_ids(ids)
        return self

    @classmethod
    def each_server(
        cls,
        *,
        servers: _Iterable[ServerCase],
        harnesses: _Iterable[HarnessCase],
        trials: int = 1,
        id: str | None = None,
    ) -> HarnessMatrix:
        return cls(
            mode="each_server",
            servers=tuple(servers),
            harnesses=tuple(harnesses),
            trials=trials,
            id=id,
        )

    @classmethod
    def all_servers(
        cls,
        *,
        servers: _Iterable[ServerCase],
        harnesses: _Iterable[HarnessCase],
        trials: int = 1,
        id: str | None = None,
    ) -> HarnessMatrix:
        return cls(
            mode="all_servers",
            servers=tuple(servers),
            harnesses=tuple(harnesses),
            trials=trials,
            id=id,
        )

    @classmethod
    def each_tool(
        cls,
        *,
        servers: _Iterable[ServerCase],
        harnesses: _Iterable[HarnessCase],
        trials: int = 1,
        id: str | None = None,
    ) -> HarnessMatrix:
        return cls(
            mode="each_tool",
            servers=tuple(servers),
            harnesses=tuple(harnesses),
            trials=trials,
            id=id,
        )

    def cases(self) -> tuple[HarnessMatrixCase, ...]:
        if self.mode == "each_server":
            return tuple(
                HarnessMatrixCase._build(
                    id=self._case_id(server.name, harness.name, trial),
                    mode=self.mode,
                    harness=harness,
                    servers=(server,),
                    trial=trial,
                    matrix_id=self.matrix_id,
                    cell_id=f"{server.name}/{harness.name}",
                    trial_count=self.trials,
                )
                for server in self.servers
                for harness in self.harnesses
                for trial in range(1, self.trials + 1)
            )
        if self.mode == "all_servers":
            servers = self.servers
            return tuple(
                HarnessMatrixCase._build(
                    id=self._case_id("all-servers", harness.name, trial),
                    mode=self.mode,
                    harness=harness,
                    servers=servers,
                    trial=trial,
                    matrix_id=self.matrix_id,
                    cell_id=f"all-servers/{harness.name}",
                    trial_count=self.trials,
                )
                for harness in self.harnesses
                for trial in range(1, self.trials + 1)
            )
        return tuple(
            HarnessMatrixCase._build(
                id=self._case_id(
                    f"{server.name}/{_tool_id(tool)}", harness.name, trial
                ),
                mode=self.mode,
                harness=harness,
                servers=(server,),
                trial=trial,
                tool=tool,
                matrix_id=self.matrix_id,
                cell_id=f"{server.name}/{_tool_id(tool)}/{harness.name}",
                trial_count=self.trials,
            )
            for server in self.servers
            for tool in server.tools
            for harness in self.harnesses
            for trial in range(1, self.trials + 1)
        )

    def _case_id(self, scope: str, harness: str, trial: int) -> str:
        base = f"{scope}/{harness}"
        return f"{base}/trial-{trial}" if self.trials > 1 else base

    def parametrize(self, argname: str = "case") -> _pytest.MarkDecorator:
        """Return pytest's ordinary parametrization decorator for this matrix."""

        return _pytest_parametrize(argname, self.cases())


class _MatrixSessionContext:
    def __init__(self, spec: _AgentSpec, kit: _MCPTestKit | None) -> None:
        self._spec = spec
        self._kit = kit
        self._owned = kit is None
        self._session: _AgentSession | None = None

    def __enter__(self) -> _AgentSession:
        selected = self._kit
        if selected is None:
            from .sync_api import MCPTestKit as _MCPTestKitRuntime

            selected = _MCPTestKitRuntime()
            self._kit = selected
        try:
            session = selected.agent_session(self._spec)
            self._session = session
            return session.__enter__()
        except BaseException:
            if self._owned:
                selected.close()
            raise

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        session = self._session
        if session is None:
            return
        try:
            session.__exit__(exc_type, exc_value, traceback)
        finally:
            if self._owned and self._kit is not None:
                self._kit.close()


class _AsyncMatrixSessionContext:
    def __init__(self, spec: _AgentSpec, kit: _AsyncMCPTestKit | None) -> None:
        self._spec = spec
        self._kit = kit
        self._owned = kit is None
        self._session: _AsyncAgentSession | None = None

    async def __aenter__(self) -> _AsyncAgentSession:
        selected = self._kit
        if selected is None:
            from .async_api import AsyncMCPTestKit as _AsyncMCPTestKitRuntime

            selected = _AsyncMCPTestKitRuntime()
            self._kit = selected
        try:
            session = selected.agent_session(self._spec)
            self._session = session
            return await session.__aenter__()
        except BaseException:
            if self._owned:
                await selected.aclose()
            raise

    async def __aexit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None:
        session = self._session
        if session is None:
            return
        try:
            await session.__aexit__(exc_type, exc_value, traceback)
        finally:
            if self._owned and self._kit is not None:
                await self._kit.aclose()


# This order is a compatibility contract for the focused wildcard surface.
__all__ = [  # noqa: RUF022
    "ToolCase",
    "ServerCase",
    "ToolMatrix",
    "ToolMatrixCase",
]
