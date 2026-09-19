"""Thin asynchronous adapter over the official MCP v2 ``ClientSession``.

This module intentionally accepts an already-created MCP session. Transport
factories, process ownership, and protocol framing remain the responsibility
of the official MCP SDK and their dedicated adapters.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, Protocol, TypeVar, cast

from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    SchemaError,
    ValidationError,
)
from mcp.shared.exceptions import MCPError as _OfficialMCPError
from pydantic import Field, model_validator
from referencing import Registry

from .direct_trace import DirectTraceBridge
from .errors import (
    ModelValidationError,
    OperationCancelled,
    OperationTimeout,
    ProtocolError,
    TransportError,
    UnsupportedFeature,
)
from .trace.redaction import RedactionConfig, redact_for_api
from .types import (
    FrozenModel,
    TraceResult,
)
from .types import (
    PromptInfo as _PublicPromptInfo,
)
from .types import (
    ResourceInfo as _PublicResourceInfo,
)
from .types import (
    TemplateInfo as _PublicTemplateInfo,
)
from .types import (
    ToolInfo as _PublicToolInfo,
)

# Backwards-compatible direct-client names share identity with the stable
# public models.  This lets converted values be used directly in durable
# operation results while retaining their process-local ``raw`` evidence.
Tool = _PublicToolInfo
Resource = _PublicResourceInfo
ResourceTemplate = _PublicTemplateInfo
Prompt = _PublicPromptInfo


class _Session(Protocol):
    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> Any: ...

    async def initialize(self) -> Any: ...

    async def list_tools(self, *, params: Any = None) -> Any: ...

    async def list_resources(self, *, params: Any = None) -> Any: ...

    async def list_resource_templates(self, *, params: Any = None) -> Any: ...

    async def read_resource(self, uri: str, **kwargs: Any) -> Any: ...

    async def list_prompts(self, *, params: Any = None) -> Any: ...

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None, **kwargs: Any
    ) -> Any: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any: ...

    async def complete(
        self,
        reference: Any,
        argument: dict[str, str],
        context_arguments: dict[str, str] | None = None,
    ) -> Any: ...

    async def subscribe_resource(self, uri: str, *, meta: Any = None) -> Any: ...

    async def unsubscribe_resource(self, uri: str, *, meta: Any = None) -> Any: ...

    async def send_ping(self, *, meta: Any = None) -> Any: ...

    async def set_logging_level(self, level: Any, *, meta: Any = None) -> Any: ...

    async def send_progress_notification(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        *,
        meta: Any = None,
    ) -> Any: ...

    async def send_notification(self, notification: Any) -> Any: ...

    async def send_roots_list_changed(self) -> Any: ...


class DirectEvidenceProvider(Protocol):
    """Supply evidence without owning execution.

    Evidence is defensively redacted and bounded by the direct client before
    it reaches error details or operation hooks, even when a provider claims
    to have performed those steps itself.
    """

    def capture(
        self, operation: str, phase: Literal["started", "succeeded", "failed"]
    ) -> Mapping[str, Any]: ...


class DirectEventHook(Protocol):
    """Observe operation boundaries for integration with a trace recorder."""

    def __call__(self, event: DirectOperationEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class ClientSessionOptions:
    """Official ``ClientSession`` constructor options for async API adapters.

    The direct core still receives an already-created session.  Transport/API
    layers can use :func:`create_client_session` to inject these callbacks at
    construction time, which is the only supported official registration path.
    """

    sampling_callback: Any = None
    elicitation_callback: Any = None
    list_roots_callback: Any = None
    logging_callback: Any = None
    message_handler: Any = None
    client_info: Any = None
    log_level: Any = None
    sampling_capabilities: Any = None
    extensions: Mapping[str, Mapping[str, Any]] | None = None
    result_claims: Any = None
    notification_bindings: Any = None
    dispatcher: Any = None


def create_client_session(
    read_stream: Any,
    write_stream: Any,
    *,
    options: ClientSessionOptions | None = None,
    read_timeout_seconds: float | None = None,
) -> Any:
    """Construct the pinned official session with callback options intact."""

    from mcp import ClientSession

    selected = options or ClientSessionOptions()
    return ClientSession(
        read_stream,
        write_stream,
        read_timeout_seconds=read_timeout_seconds,
        sampling_callback=selected.sampling_callback,
        elicitation_callback=selected.elicitation_callback,
        list_roots_callback=selected.list_roots_callback,
        logging_callback=selected.logging_callback,
        message_handler=selected.message_handler,
        client_info=selected.client_info,
        log_level=selected.log_level,
        sampling_capabilities=selected.sampling_capabilities,
        extensions=cast(dict[str, dict[str, Any]], dict(selected.extensions))
        if selected.extensions is not None
        else None,
        result_claims=selected.result_claims,
        notification_bindings=selected.notification_bindings,
        dispatcher=selected.dispatcher,
    )


class _RawValue(FrozenModel):
    # Raw SDK objects are intentionally non-serializable evidence.  ``None``
    # permits safe public-model round trips after that evidence is excluded.
    raw: Any = Field(default=None, exclude=True, repr=False)


class DirectOperationEvent(FrozenModel):
    """A narrow, ownership-free hook event for stable trace integration."""

    operation: str
    phase: Literal["started", "succeeded", "failed"]
    evidence: Mapping[str, Any] = Field(default_factory=dict)
    error_kind: str | None = None


class InitializationResult(_RawValue):
    protocol_version: str
    server_info: Mapping[str, Any]
    instructions: str | None = None
    capabilities: Mapping[str, Any] = Field(default_factory=dict)


class ToolsPage(_RawValue):
    tools: tuple[Tool, ...] = ()
    next_cursor: str | None = None


class ResourcesPage(_RawValue):
    resources: tuple[Resource, ...] = ()
    next_cursor: str | None = None


class ResourceTemplatesPage(_RawValue):
    resource_templates: tuple[ResourceTemplate, ...] = ()
    next_cursor: str | None = None


class PromptsPage(_RawValue):
    prompts: tuple[Prompt, ...] = ()
    next_cursor: str | None = None


class ResourceReadResult(_RawValue):
    contents: tuple[Mapping[str, Any], ...] = ()

    @property
    def text(self) -> str:
        return "".join(
            str(item.get("text", ""))
            for item in self.contents
            if item.get("type") == "text" or "text" in item
        )


class InputRequiredResult(_RawValue):
    """Official MCP interactive result, preserved instead of coercing empty data."""

    result_type: Literal["input_required"] = "input_required"
    input_requests: Mapping[str, Any] | None = None
    request_state: str | None = None

    @model_validator(mode="after")
    def _requires_input_signal(self) -> InputRequiredResult:
        if self.input_requests is None and self.request_state is None:
            raise ValueError(
                "input_required results need input_requests or request_state"
            )
        return self


class PromptResult(_RawValue):
    description: str | None = None
    messages: tuple[Mapping[str, Any], ...] = ()


class ToolCallResult(_RawValue):
    content: tuple[Mapping[str, Any], ...] = ()
    structured_content: Any = None
    is_error: bool = False


class CompletionResult(_RawValue):
    values: tuple[str, ...] = ()
    total: int | None = None
    has_more: bool | None = None


class EmptyResult(_RawValue):
    result_type: str | None = None


_T = TypeVar("_T")


def _plain_json(value: Any) -> Any:
    """Convert SDK frozen mappings to ordinary JSON containers."""

    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return cast(dict[str, Any], _plain_json(value))
    method = getattr(value, "model_dump", None)
    if callable(method):
        dumped = method(mode="json", by_alias=True)
        if isinstance(dumped, Mapping):
            return cast(dict[str, Any], _plain_json(dumped))
    return {}


def _attribute(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
        camel = name.split("_")[0] + "".join(
            part.title() for part in name.split("_")[1:]
        )
        return value.get(camel, default)
    return getattr(value, name, default)


def _raw_content(value: Any) -> tuple[Mapping[str, Any], ...]:
    content = _attribute(value, "content", ())
    if not isinstance(content, Sequence) or isinstance(
        content, (str, bytes, bytearray)
    ):
        return ()
    return tuple(_dump(item) for item in content)


def _input_required(value: Any) -> InputRequiredResult | None:
    if (
        _attribute(value, "result_type") != "input_required"
        and _attribute(value, "resultType") != "input_required"
    ):
        return None
    requests = _attribute(value, "input_requests")
    if requests is None:
        requests = _attribute(value, "inputRequests")
    plain_requests = _plain_json(requests) if isinstance(requests, Mapping) else None
    return InputRequiredResult(
        raw=value,
        input_requests=cast(Mapping[str, Any] | None, plain_requests),
        request_state=cast(
            str | None,
            _attribute(value, "request_state", _attribute(value, "requestState")),
        ),
    )


def _tool(value: Any) -> Tool:
    return Tool(
        raw=value,
        name=str(_attribute(value, "name", "")),
        title=_attribute(value, "title"),
        description=_attribute(value, "description"),
        input_schema=cast(
            Mapping[str, Any] | bool, _attribute(value, "input_schema", {})
        ),
        output_schema=cast(
            Mapping[str, Any] | bool | None, _attribute(value, "output_schema")
        ),
    )


def _resource(value: Any) -> Resource:
    return Resource(
        raw=value,
        name=str(_attribute(value, "name", "")),
        title=_attribute(value, "title"),
        uri=str(_attribute(value, "uri", "")),
        description=_attribute(value, "description"),
        mime_type=_attribute(value, "mime_type"),
        size=_attribute(value, "size"),
    )


def _resource_template(value: Any) -> ResourceTemplate:
    return ResourceTemplate(
        raw=value,
        name=str(_attribute(value, "name", "")),
        title=_attribute(value, "title"),
        uri_template=str(_attribute(value, "uri_template", "")),
        description=_attribute(value, "description"),
        mime_type=_attribute(value, "mime_type"),
    )


def _prompt(value: Any) -> Prompt:
    arguments = _attribute(value, "arguments") or ()
    return Prompt(
        raw=value,
        name=str(_attribute(value, "name", "")),
        title=_attribute(value, "title"),
        description=_attribute(value, "description"),
        arguments=tuple(_dump(item) for item in arguments),
    )


def _params(cursor: str | None) -> Any:
    from mcp.types import PaginatedRequestParams

    return PaginatedRequestParams(cursor=cursor) if cursor is not None else None


def _schema_failure(operation: str, tool: str, kind: str) -> ModelValidationError:
    """Return a safe schema error without echoing untrusted schema or data."""

    return ModelValidationError(
        "tool JSON Schema validation failed",
        details={"operation": operation, "tool": tool, "kind": kind},
    )


def _is_official_result_validation(error: BaseException) -> bool:
    """Recognize the official client's safe result-schema failure shape."""

    candidate: BaseException | None = error
    while candidate is not None:
        if isinstance(candidate, RuntimeError) and str(candidate).startswith(
            "Invalid structured content returned by tool "
        ):
            return True
        candidate = candidate.__cause__ or candidate.__context__
    return False


def _validate_json_schema(
    *,
    value: Any,
    schema: Mapping[str, Any] | bool,
    operation: str,
    tool: str,
    kind: str,
) -> None:
    """Validate with Draft 2020-12 while refusing remote reference fetches.

    The registry's default retrieval handler fails rather than performing I/O.
    Local references (for example ``#/$defs/value``) remain fully supported.
    """

    try:
        plain_schema = cast(Mapping[str, Any] | bool, _plain_json(schema))
        Draft202012Validator.check_schema(plain_schema)
        validator = Draft202012Validator(plain_schema, registry=Registry())
        validator.validate(_plain_json(value))
    except SchemaError as exc:
        raise _schema_failure(operation, tool, "invalid_schema") from exc
    except ValidationError as exc:
        raise _schema_failure(operation, tool, kind) from exc
    except Exception as exc:
        # Unresolvable references and malformed validator inputs are reported
        # as schema failures; details deliberately exclude exception text.
        raise _schema_failure(operation, tool, "invalid_schema") from exc


def _bounded_evidence(value: Any, depth: int = 0) -> Any:
    """Keep provider evidence JSON-safe, bounded, and value-shape limited."""

    if depth > 3:
        return "<omitted>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, Mapping):
        return {
            str(key)[:128]: _bounded_evidence(item, depth + 1)
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_evidence(item, depth + 1) for item in value[:32]]
    return "<omitted>"


class AsyncDirectClient:
    """Typed async facade over one official MCP ``ClientSession``."""

    def __init__(
        self,
        session: _Session,
        *,
        timeout: float = 30.0,
        validate_schemas: bool = False,
        evidence_provider: DirectEvidenceProvider | None = None,
        event_hook: DirectEventHook | None = None,
        trace_bridge: DirectTraceBridge | None = None,
        redaction_config: RedactionConfig | None = None,
        workspace_root: str | None = None,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ModelValidationError(
                "timeout must be positive and finite", details={"operation": "client"}
            )
        self._session = session
        self._timeout = timeout
        self._validate_schemas = validate_schemas
        self._evidence_provider = evidence_provider
        self._event_hook = event_hook
        self._trace_bridge = trace_bridge
        self._redaction_config = redaction_config or RedactionConfig.from_environment()
        self._workspace_root = workspace_root
        self._entered = False
        self._closed = False
        self._initialized: InitializationResult | None = None

    @property
    def initialization(self) -> InitializationResult | None:
        return self._initialized

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def trace(self) -> TraceResult | None:
        """Immutable live stable trace when a bridge is attached."""

        return self._trace_bridge.trace if self._trace_bridge is not None else None

    @property
    def final_trace(self) -> TraceResult | None:
        """Immutable finalized stable trace, if available."""

        return (
            self._trace_bridge.final_trace if self._trace_bridge is not None else None
        )

    def _trace_evidence(self) -> Mapping[str, Any] | None:
        if self._trace_bridge is None:
            return None
        try:
            return self._trace_bridge.partial_evidence()
        except Exception:
            return {
                "evidence_mode": "unavailable",
                "limitations": ("capture_incomplete",),
            }

    async def __aenter__(self) -> AsyncDirectClient:
        if self._closed:
            raise RuntimeError("direct client is closed")
        if self._entered:
            return self
        try:
            # Treat the session context handshake as a protocol operation so
            # transport/setup exceptions do not escape as implementation
            # details or leak their message.
            await self._execute("session/enter", self._session.__aenter__())
            self._entered = True
            await self.initialize()
            return self
        except BaseException:
            if self._entered:
                await self._session.__aexit__(None, None, None)
                self._entered = False
            raise

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._entered:
            await self._session.__aexit__(None, None, None)
            self._entered = False

    def _require_open(self) -> None:
        if self._closed or not self._entered:
            raise RuntimeError("direct client must be entered before use")

    def _evidence(
        self, operation: str, phase: Literal["started", "succeeded", "failed"]
    ) -> Mapping[str, Any]:
        if self._evidence_provider is None:
            return {"operation": operation, "phase": phase, "captured": False}
        try:
            supplied = self._evidence_provider.capture(operation, phase)
            if not isinstance(supplied, Mapping):
                return {"operation": operation, "phase": phase, "captured": False}
            # Redact before any bounding/string conversion.  Provider output
            # is untrusted and may contain credentials or hostile containers;
            # a failure must not fall back to the original value.
            redacted = redact_for_api(
                supplied,
                config=self._redaction_config,
                path="$.direct_evidence",
            )
            bounded = _bounded_evidence(redacted)
            if isinstance(bounded, Mapping):
                # Provider data is evidence, not event metadata.  Reserved
                # keys must remain authoritative even when a provider is
                # backed by an untrusted transport or test fixture.
                safe_data = {
                    str(key): item
                    for key, item in bounded.items()
                    if str(key) not in {"operation", "phase", "captured"}
                }
                return {
                    "operation": operation,
                    "phase": phase,
                    "captured": True,
                    **safe_data,
                }
        except Exception:
            pass
        return {"operation": operation, "phase": phase, "captured": False}

    def _emit_event(
        self,
        operation: str,
        phase: Literal["started", "succeeded", "failed"],
        *,
        error_kind: str | None = None,
    ) -> None:
        if self._event_hook is None:
            return
        try:
            self._event_hook(
                DirectOperationEvent(
                    operation=operation,
                    phase=phase,
                    evidence=self._evidence(operation, phase),
                    error_kind=error_kind,
                )
            )
        except Exception:
            # Instrumentation cannot change protocol semantics or hide the
            # original operation outcome.
            return

    def _protocol_failure(self, operation: str, error: BaseException) -> ProtocolError:
        official_error = isinstance(error, _OfficialMCPError)
        details: dict[str, Any] = {
            "operation": operation,
            "phase": "protocol",
            "partial_evidence": self._evidence(operation, "failed"),
            "error_kind": "mcp_protocol" if official_error else "client_exception",
        }
        trace_evidence = self._trace_evidence()
        if trace_evidence is not None:
            details["trace_evidence"] = trace_evidence
        if isinstance(error, _OfficialMCPError):
            details["protocol_code"] = int(error.code)
        else:
            if isinstance(error, TypeError):
                details["exception_type"] = "TypeError"
            elif isinstance(error, ValueError):
                details["exception_type"] = "ValueError"
            elif isinstance(error, RuntimeError):
                details["exception_type"] = "RuntimeError"
            else:
                details["exception_type"] = "Exception"
        self._emit_event(operation, "failed", error_kind="protocol")
        return ProtocolError(
            (
                f"MCP protocol operation failed: {operation}"
                if official_error
                else f"MCP client operation failed: {operation}"
            ),
            details=details,
        )

    def _transport_failure(
        self, operation: str, error: BaseException
    ) -> TransportError:
        del error
        self._emit_event(operation, "failed", error_kind="transport")
        return TransportError(
            f"MCP transport operation failed: {operation}",
            details={
                "operation": operation,
                "phase": "transport",
                "partial_evidence": self._evidence(operation, "failed"),
                "error_kind": "transport",
                **(
                    {"trace_evidence": self._trace_evidence()}
                    if self._trace_evidence() is not None
                    else {}
                ),
            },
        )

    def _timeout_failure(self, operation: str, timeout: float) -> OperationTimeout:
        self._emit_event(operation, "failed", error_kind="timeout")
        return OperationTimeout(
            f"MCP operation timed out: {operation}",
            details={
                "operation": operation,
                "phase": "protocol",
                "timeout_seconds": timeout,
                "partial_evidence": self._evidence(operation, "failed"),
                **(
                    {"trace_evidence": self._trace_evidence()}
                    if self._trace_evidence() is not None
                    else {}
                ),
            },
        )

    def _cancelled_failure(self, operation: str) -> OperationCancelled:
        self._emit_event(operation, "failed", error_kind="cancelled")
        return OperationCancelled(
            f"MCP operation cancelled: {operation}",
            details={
                "operation": operation,
                "phase": "protocol",
                "partial_evidence": self._evidence(operation, "failed"),
                **(
                    {"trace_evidence": self._trace_evidence()}
                    if self._trace_evidence() is not None
                    else {}
                ),
            },
        )

    @staticmethod
    def _is_transport_exception(error: BaseException) -> bool:
        if isinstance(error, (OSError, ConnectionError, EOFError)):
            return True
        module = type(error).__module__
        return module.startswith(("anyio", "httpx", "httpcore"))

    async def _execute(
        self, operation: str, awaitable: Awaitable[_T], timeout: float | None = None
    ) -> _T:
        effective_timeout = self._timeout if timeout is None else timeout
        if not math.isfinite(effective_timeout) or effective_timeout <= 0:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise ModelValidationError(
                "timeout must be positive", details={"operation": operation}
            )
        self._emit_event(operation, "started")
        try:
            # ClientSession owns an AnyIO cancel scope entered by its
            # __aenter__; wrapping that particular awaitable in wait_for would
            # move ownership to a child asyncio task and make official cleanup
            # fail. Protocol initialization and requests remain bounded below.
            if operation == "session/enter":
                result = await awaitable
            else:
                result = await asyncio.wait_for(awaitable, timeout=effective_timeout)
            self._emit_event(operation, "succeeded")
            return result
        except asyncio.TimeoutError as exc:
            raise self._timeout_failure(operation, effective_timeout) from exc
        except asyncio.CancelledError as exc:
            raise self._cancelled_failure(operation) from exc
        except ProtocolError:
            self._emit_event(operation, "failed", error_kind="protocol")
            raise
        except TransportError:
            self._emit_event(operation, "failed", error_kind="transport")
            raise
        except OperationTimeout:
            self._emit_event(operation, "failed", error_kind="timeout")
            raise
        except OperationCancelled:
            self._emit_event(operation, "failed", error_kind="cancelled")
            raise
        except _OfficialMCPError as exc:
            if exc.code == -32001:
                raise self._timeout_failure(operation, effective_timeout) from exc
            if exc.code == -32000:
                raise self._transport_failure(operation, exc) from exc
            raise self._protocol_failure(operation, exc) from exc
        except Exception as exc:
            if self._is_transport_exception(exc):
                raise self._transport_failure(operation, exc) from exc
            raise self._protocol_failure(operation, exc) from exc

    async def initialize(self) -> InitializationResult:
        self._require_open()
        if self._initialized is not None:
            return self._initialized
        try:
            raw = await self._execute("initialize", self._session.initialize())
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        info = _dump(_attribute(raw, "server_info", {}))
        result = InitializationResult(
            raw=raw,
            protocol_version=str(_attribute(raw, "protocol_version", "")),
            server_info=info,
            instructions=_attribute(raw, "instructions"),
            capabilities=_dump(_attribute(raw, "capabilities", {})),
        )
        self._initialized = result
        return result

    async def list_tools(self, *, cursor: str | None = None) -> ToolsPage:
        self._require_open()
        try:
            raw = await self._execute(
                "tools/list", self._session.list_tools(params=_params(cursor))
            )
            return ToolsPage(
                raw=raw,
                tools=tuple(_tool(item) for item in _attribute(raw, "tools", ())),
                next_cursor=_attribute(raw, "next_cursor"),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("tools/list", exc) from exc

    async def list_all_tools(self) -> tuple[Tool, ...]:
        pages: list[Tool] = []
        async for page in self._pages(self.list_tools):
            pages.extend(page.tools)
        return tuple(pages)

    async def list_resources(self, *, cursor: str | None = None) -> ResourcesPage:
        self._require_open()
        try:
            raw = await self._execute(
                "resources/list", self._session.list_resources(params=_params(cursor))
            )
            return ResourcesPage(
                raw=raw,
                resources=tuple(
                    _resource(item) for item in _attribute(raw, "resources", ())
                ),
                next_cursor=_attribute(raw, "next_cursor"),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("resources/list", exc) from exc

    async def list_all_resources(self) -> tuple[Resource, ...]:
        pages: list[Resource] = []
        async for page in self._pages(self.list_resources):
            pages.extend(page.resources)
        return tuple(pages)

    async def list_resource_templates(
        self, *, cursor: str | None = None
    ) -> ResourceTemplatesPage:
        self._require_open()
        try:
            raw = await self._execute(
                "resources/templates/list",
                self._session.list_resource_templates(params=_params(cursor)),
            )
            return ResourceTemplatesPage(
                raw=raw,
                resource_templates=tuple(
                    _resource_template(item)
                    for item in _attribute(raw, "resource_templates", ())
                ),
                next_cursor=_attribute(raw, "next_cursor"),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("resources/templates/list", exc) from exc

    async def list_all_resource_templates(self) -> tuple[ResourceTemplate, ...]:
        pages: list[ResourceTemplate] = []
        async for page in self._pages(self.list_resource_templates):
            pages.extend(page.resource_templates)
        return tuple(pages)

    async def list_prompts(self, *, cursor: str | None = None) -> PromptsPage:
        self._require_open()
        try:
            raw = await self._execute(
                "prompts/list", self._session.list_prompts(params=_params(cursor))
            )
            return PromptsPage(
                raw=raw,
                prompts=tuple(_prompt(item) for item in _attribute(raw, "prompts", ())),
                next_cursor=_attribute(raw, "next_cursor"),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("prompts/list", exc) from exc

    async def list_all_prompts(self) -> tuple[Prompt, ...]:
        pages: list[Prompt] = []
        async for page in self._pages(self.list_prompts):
            pages.extend(page.prompts)
        return tuple(pages)

    async def _pages(self, method: Any) -> Any:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            page = await method(cursor=cursor)
            yield page
            cursor = page.next_cursor
            if cursor is None:
                return
            if cursor in seen:
                raise ProtocolError(
                    "MCP pagination cursor repeated",
                    details={"phase": "pagination", "retryable": False},
                )
            seen.add(cursor)

    async def read_resource(
        self,
        uri: str,
        *,
        input_responses: Any = None,
        request_state: str | None = None,
        meta: Any = None,
        allow_input_required: bool = False,
    ) -> ResourceReadResult | InputRequiredResult:
        self._require_open()
        try:
            raw = await self._execute(
                "resources/read",
                self._session.read_resource(
                    uri,
                    input_responses=input_responses,
                    request_state=request_state,
                    meta=meta,
                    allow_input_required=allow_input_required,
                ),
            )
            required = _input_required(raw)
            if required is not None:
                return required
            contents = tuple(_dump(item) for item in _attribute(raw, "contents", ()))
            return ResourceReadResult(raw=raw, contents=contents)
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("resources/read", exc) from exc

    async def get_prompt(
        self,
        name: str,
        arguments: Mapping[str, str] | None = None,
        *,
        input_responses: Any = None,
        request_state: str | None = None,
        meta: Any = None,
        allow_input_required: bool = False,
    ) -> PromptResult | InputRequiredResult:
        self._require_open()
        try:
            raw = await self._execute(
                "prompts/get",
                self._session.get_prompt(
                    name,
                    dict(arguments) if arguments is not None else None,
                    input_responses=input_responses,
                    request_state=request_state,
                    meta=meta,
                    allow_input_required=allow_input_required,
                ),
            )
            required = _input_required(raw)
            if required is not None:
                return required
            return PromptResult(
                raw=raw,
                description=_attribute(raw, "description"),
                messages=tuple(_dump(item) for item in _attribute(raw, "messages", ())),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("prompts/get", exc) from exc

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
        progress_callback: Any = None,
        input_responses: Any = None,
        request_state: str | None = None,
        meta: Any = None,
        allow_input_required: bool = False,
        allow_claimed: bool = False,
    ) -> ToolCallResult | InputRequiredResult:
        self._require_open()
        values = cast(dict[str, Any], _plain_json(arguments or {}))
        effective_timeout = self._timeout if timeout is None else timeout
        if self._validate_schemas:
            tools = await self.list_all_tools()
            tool = next((item for item in tools if item.name == name), None)
            if tool is not None:
                _validate_json_schema(
                    value=values,
                    schema=tool.input_schema,
                    operation="tools/call",
                    tool=name,
                    kind="invalid_arguments",
                )
        try:
            raw = await self._execute(
                "tools/call",
                self._session.call_tool(
                    name,
                    values,
                    read_timeout_seconds=effective_timeout,
                    progress_callback=progress_callback,
                    input_responses=input_responses,
                    request_state=request_state,
                    meta=meta,
                    allow_input_required=allow_input_required,
                    allow_claimed=allow_claimed,
                ),
                effective_timeout,
            )
            required = _input_required(raw)
            if required is not None:
                return required
            result = ToolCallResult(
                raw=raw,
                content=_raw_content(raw),
                structured_content=_attribute(raw, "structured_content"),
                is_error=bool(_attribute(raw, "is_error", False)),
            )
            if self._validate_schemas:
                tools = await self.list_all_tools()
                tool = next((item for item in tools if item.name == name), None)
                if tool is not None and tool.output_schema is not None:
                    _validate_json_schema(
                        value=result.structured_content,
                        schema=tool.output_schema,
                        operation="tools/call",
                        tool=name,
                        kind="invalid_result",
                    )
            return result
        except ModelValidationError as error:
            # Include the normalized trace identity on local schema failures;
            # payloads remain in the trace's redaction-bound evidence store.
            if "trace_evidence" not in error.details:
                trace_evidence = self._trace_evidence()
                if trace_evidence is not None:
                    error.details["trace_evidence"] = trace_evidence
            raise
        except ProtocolError as error:
            # mcp==2.0.0 validates outputSchema inside ClientSession as well
            # as the server path.  A decoded fault therefore arrives as a
            # sanitized RuntimeError wrapped by our protocol mapper; expose
            # the public model-validation contract instead of leaking that
            # implementation detail or its schema/data excerpt.
            if self._validate_schemas and _is_official_result_validation(error):
                self._emit_event("tools/call", "failed", error_kind="model_validation")
                details: dict[str, Any] = {
                    "operation": "tools/call",
                    "tool": name,
                    "kind": "invalid_result",
                }
                trace_evidence = self._trace_evidence()
                if trace_evidence is not None:
                    details["trace_evidence"] = trace_evidence
                raise ModelValidationError(
                    "tool JSON Schema validation failed", details=details
                ) from None
            raise
        except (TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("tools/call", exc) from exc

    async def complete(
        self,
        reference: Any,
        argument: Mapping[str, str],
        context_arguments: Mapping[str, str] | None = None,
    ) -> CompletionResult:
        """Request server completion through the official ClientSession."""

        self._require_open()
        try:
            raw = await self._execute(
                "completion/complete",
                self._session.complete(
                    reference,
                    dict(argument),
                    dict(context_arguments) if context_arguments is not None else None,
                ),
            )
            completion = _attribute(raw, "completion")
            return CompletionResult(
                raw=raw,
                values=tuple(
                    str(value) for value in (_attribute(completion, "values", ()) or ())
                ),
                total=_attribute(completion, "total"),
                has_more=_attribute(completion, "has_more"),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("completion/complete", exc) from exc

    async def subscribe_resource(self, uri: str, *, meta: Any = None) -> EmptyResult:
        self._require_open()
        try:
            raw = await self._execute(
                "resources/subscribe", self._session.subscribe_resource(uri, meta=meta)
            )
            return EmptyResult(raw=raw, result_type=_attribute(raw, "result_type"))
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("resources/subscribe", exc) from exc

    async def unsubscribe_resource(self, uri: str, *, meta: Any = None) -> EmptyResult:
        self._require_open()
        try:
            raw = await self._execute(
                "resources/unsubscribe",
                self._session.unsubscribe_resource(uri, meta=meta),
            )
            return EmptyResult(raw=raw, result_type=_attribute(raw, "result_type"))
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("resources/unsubscribe", exc) from exc

    async def ping(self, *, meta: Any = None) -> EmptyResult:
        self._require_open()
        try:
            raw = await self._execute("ping", self._session.send_ping(meta=meta))
            return EmptyResult(raw=raw, result_type=_attribute(raw, "result_type"))
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("ping", exc) from exc

    async def set_logging_level(self, level: str, *, meta: Any = None) -> EmptyResult:
        self._require_open()
        allowed = {
            "debug",
            "info",
            "notice",
            "warning",
            "error",
            "critical",
            "alert",
            "emergency",
        }
        if level not in allowed:
            raise ModelValidationError(
                "unsupported logging level", details={"operation": "logging/setLevel"}
            )
        try:
            raw = await self._execute(
                "logging/setLevel",
                self._session.set_logging_level(cast(Any, level), meta=meta),
            )
            return EmptyResult(raw=raw, result_type=_attribute(raw, "result_type"))
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("logging/setLevel", exc) from exc

    async def send_progress_notification(
        self,
        progress_token: str | int,
        progress: float,
        total: float | None = None,
        message: str | None = None,
        *,
        meta: Any = None,
    ) -> None:
        self._require_open()
        if not math.isfinite(progress) or (
            total is not None and not math.isfinite(total)
        ):
            raise ModelValidationError(
                "progress values must be finite",
                details={"operation": "notifications/progress"},
            )
        try:
            await self._execute(
                "notifications/progress",
                self._session.send_progress_notification(
                    progress_token, progress, total, message, meta=meta
                ),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("notifications/progress", exc) from exc

    async def send_notification(self, notification: Any) -> None:
        self._require_open()
        try:
            await self._execute(
                "notification", self._session.send_notification(notification)
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure("notification", exc) from exc

    async def send_roots_list_changed(self) -> None:
        self._require_open()
        try:
            await self._execute(
                "notifications/roots/list_changed",
                self._session.send_roots_list_changed(),
            )
        except (ProtocolError, TransportError, OperationTimeout, OperationCancelled):
            raise
        except Exception as exc:
            raise self._protocol_failure(
                "notifications/roots/list_changed", exc
            ) from exc

    def register_callbacks(self, **callbacks: Any) -> NoReturn:
        """Reject post-construction callback mutation explicitly.

        The official ClientSession accepts sampling, elicitation, roots,
        logging, and message callbacks only in its constructor. Mutating its
        dispatcher here would duplicate protocol ownership and is forbidden.
        """

        del callbacks
        raise UnsupportedFeature(
            "MCP callbacks must be supplied when constructing ClientSession",
            details={"operation": "callback_registration", "phase": "preflight"},
        )


# Explicit SDK names avoid confusing these wrappers with the official MCP
# classes while keeping familiar result names available to callers.
# Public serializable values are defined in ``m3.types`` so that
# the type exported by the direct-client adapters is identical to the durable
# model used by execution results.  The process-local ``Tool``/``Resource``/
# ``Prompt`` aliases retain their excluded ``raw`` evidence at the direct
# protocol boundary.
ToolInfo = _PublicToolInfo
ResourceInfo = _PublicResourceInfo
TemplateInfo = _PublicTemplateInfo
PromptInfo = _PublicPromptInfo
ToolPage = ToolsPage
ResourcePage = ResourcesPage
ResourceTemplatePage = ResourceTemplatesPage
PromptPage = PromptsPage
InitializeResult = InitializationResult
ListToolsResult = ToolsPage
ListResourcesResult = ResourcesPage
ListResourceTemplatesResult = ResourceTemplatesPage
ListPromptsResult = PromptsPage
ReadResourceResult = ResourceReadResult
GetPromptResult = PromptResult
CallToolResult = ToolCallResult


__all__ = [
    "AsyncDirectClient",
    "CallToolResult",
    "ClientSessionOptions",
    "DirectEventHook",
    "DirectEvidenceProvider",
    "DirectOperationEvent",
    "GetPromptResult",
    "InitializationResult",
    "InitializeResult",
    "InputRequiredResult",
    "ListPromptsResult",
    "ListResourceTemplatesResult",
    "ListResourcesResult",
    "ListToolsResult",
    "Prompt",
    "PromptInfo",
    "PromptPage",
    "PromptResult",
    "PromptsPage",
    "Resource",
    "ResourceInfo",
    "ResourcePage",
    "ResourceReadResult",
    "ResourceTemplate",
    "ResourceTemplatePage",
    "ResourceTemplatesPage",
    "ResourcesPage",
    "TemplateInfo",
    "Tool",
    "ToolCallResult",
    "ToolInfo",
    "ToolPage",
    "ToolsPage",
    "create_client_session",
]
