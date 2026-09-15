"""Application ACP probes over the SDK contract and real ACP adapter probes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol, cast

from mcp_pal.harness.acp import full_probe, protocol_probe
from mcp_pal.services.acp_probes import (
    ACPAgentMode,
    ACPProbeDimension,
    ACPProbeHistory,
    ACPProbeKind,
    ACPProbeRequest,
    ACPProbeResult,
    ACPProbeStatus,
    ACPProbeStore,
    JsonValue,
    Runner,
    run_acp_probe,
)


class _ProfileStore(ACPProbeStore, Protocol):
    def get_profile(self, profile_id: str, *, kind: str | None = None) -> Any: ...
    def get_revision(self, revision_id: str, *, kind: str | None = None) -> Any: ...


def _option_id(option: Mapping[str, object]) -> str | None:
    value = option.get("id", option.get("configId"))
    return str(value) if value is not None else None


def _option_values(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        output: list[object] = []
        for item in value:
            if isinstance(item, Mapping) and "value" in item:
                output.append(item["value"])
            elif isinstance(item, Mapping) and isinstance(
                item.get("options"), (list, tuple)
            ):
                output.extend(_option_values(item["options"]))
            else:
                output.append(item)
        return tuple(output)
    return ()


class ACPProbes:
    """Persist ACP probe lifecycle and expose exact-dimension history."""

    def __init__(self, store: ACPProbeStore, runner: Runner | None = None) -> None:
        self.store = store
        self.runner = runner if runner is not None else self._run_current_manifest
        self._closed = False
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mcp-pal-acp-probe"
        )
        self._lock = RLock()
        self._futures: dict[str, Future[None]] = {}
        self._tasks: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Task[object]]
        ] = {}

    def _ensure_open(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("ACP probe service is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            # Mark every outstanding observation terminal before closing the
            # store. A worker that is between protocol calls will see this
            # state and retain it rather than writing a late result.
            futures = tuple(self._futures.values())
            for future in futures:
                future.cancel()
            for loop, task in self._tasks.values():
                if not task.done():
                    loop.call_soon_threadsafe(task.cancel)
            for probe_id in tuple(self._futures):
                current = self.store.get_acp_probe(probe_id)
                if current is not None and current.status in {
                    ACPProbeStatus.QUEUED,
                    ACPProbeStatus.RUNNING,
                }:
                    self.store.save_acp_probe(
                        current.model_copy(
                            update={
                                "status": ACPProbeStatus.CANCELLED,
                                "finished_at": datetime.now(timezone.utc),
                                "error": "probe cancelled during shutdown",
                            }
                        )
                    )
            self._closed = True
        # ACP subprocess probes clean up their process in their coroutine
        # finally blocks. Give active workers a bounded opportunity to finish;
        # futures that were never started are cancelled immediately.
        wait(futures, timeout=5.0)
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._lock:
            self._futures.clear()
            self._tasks.clear()

    def _current_profile_revision(self, request: ACPProbeRequest) -> tuple[Any, Any]:
        store = cast(_ProfileStore, self.store)
        profile = store.get_profile(request.profile_id, kind="harness")
        revision = store.get_revision(request.revision_id, kind="harness")
        current = getattr(profile, "current_revision_id", None)
        current_id = (
            str(getattr(current, "root", current)) if current is not None else None
        )
        if profile is None or getattr(profile, "kind", None) != "harness":
            raise ValueError("ACP harness profile does not exist")
        if getattr(profile, "archived", False):
            raise ValueError("ACP harness profile is archived")
        if (
            revision is None
            or getattr(revision, "profile_id", None) != request.profile_id
        ):
            raise ValueError("ACP harness revision does not belong to profile")
        if current_id != request.revision_id:
            raise ValueError("ACP harness revision is not current")
        return profile, revision

    @staticmethod
    def _manifest(revision: Any) -> dict[str, Any]:
        value = getattr(revision, "value", {})
        manifest = value.get("manifest", value) if isinstance(value, Mapping) else {}
        if not isinstance(manifest, Mapping):
            raise ValueError("ACP harness manifest is invalid")
        return dict(manifest)

    async def _run_current_manifest(
        self, request: ACPProbeRequest
    ) -> Mapping[str, Any]:
        _, revision = self._current_profile_revision(request)
        manifest = self._manifest(revision)
        if request.probe_type is ACPProbeKind.PROTOCOL:
            # Protocol negotiation has its own SDK safety deadline (5s). The
            # request timeout remains the outer lifecycle bound in run_acp_probe;
            # it must not turn a protocol probe into an arbitrarily long child.
            raw = await protocol_probe(manifest)
        else:
            raw = await full_probe(
                manifest,
                mode_id=request.agent_mode_id,
                session_config=dict(request.session_config),
                transport=request.transport,
                timeout_seconds=request.timeout_seconds,
            )
        # protocol_probe calls the field ``agent_info`` while full_probe uses
        # ``agent_identity``.  Explicitly lift both plus capabilities/options,
        # and put every other adapter observation under bounded ``evidence``;
        # run_acp_probe intentionally ignores unknown top-level runner keys.
        reserved = {
            "status",
            "agent_identity",
            "agent_info",
            "agent_capabilities",
            "config_options",
            "evidence",
            "diagnostics",
            "error",
        }
        evidence = (
            dict(raw.get("evidence", {}))
            if isinstance(raw.get("evidence"), Mapping)
            else {}
        )
        evidence.update(
            {str(key): value for key, value in raw.items() if key not in reserved}
        )
        frame_results: list[Mapping[str, Any]] = []
        frames = raw.get("frames", ())
        if isinstance(frames, (list, tuple)):
            for frame in frames:
                if not isinstance(frame, Mapping):
                    continue
                payload = frame.get("payload")
                response = (
                    payload.get("result") if isinstance(payload, Mapping) else None
                )
                if isinstance(response, Mapping):
                    frame_results.append(response)
        wire_session = next(
            (
                response
                for response in frame_results
                if response.get("modes") is not None
                or response.get("configOptions") is not None
                or response.get("config_options") is not None
            ),
            {},
        )
        wire_identity = next(
            (
                response.get("agentInfo", response.get("agent_info"))
                for response in frame_results
                if response.get("agentInfo") is not None
                or response.get("agent_info") is not None
            ),
            None,
        )
        raw_identity = (
            raw.get("agent_identity") or raw.get("agent_info") or wire_identity
        )
        raw_modes = raw.get("modes") or wire_session.get("modes", ())
        if isinstance(raw_modes, Mapping):
            advertised = raw_modes.get(
                "availableModes", raw_modes.get("available_modes", ())
            )
            current = raw_modes.get("currentModeId", raw_modes.get("current_mode_id"))
        else:
            advertised, current = raw_modes, None
        raw_config_options = raw.get(
            "config_options", raw.get("configOptions", ())
        ) or wire_session.get("configOptions", wire_session.get("config_options", ()))
        # The packaged ACP client currently exposes session config options
        # only in the raw session/new frame (its typed response projection is
        # intentionally conservative).  Promote that wire field before the
        # generic evidence projection so direct SDK clients retain the
        # advertised option descriptors.
        result: dict[str, Any] = {
            "status": raw.get("status", "failed"),
            "agent_identity": raw_identity,
            "agent_capabilities": raw.get("agent_capabilities", {}),
            "agent_modes": tuple(
                ACPAgentMode.model_validate(
                    {"id": item.get("id"), "name": item.get("name", item.get("id"))}
                )
                for item in (
                    advertised if isinstance(advertised, (list, tuple)) else ()
                )
                if isinstance(item, Mapping) and item.get("id") is not None
            ),
            "current_agent_mode_id": str(current)
            if isinstance(current, (str, int, float))
            else None,
            "config_options": raw_config_options,
            "evidence": evidence,
        }
        for key in ("diagnostics", "error"):
            if key in raw:
                result[key] = raw[key]
        return result

    @staticmethod
    def _protocol_evidence(result: ACPProbeResult) -> Mapping[str, object]:
        return cast(Mapping[str, object], result.evidence)

    def _normalize_full_request(self, request: ACPProbeRequest) -> ACPProbeRequest:
        if request.probe_type is not ACPProbeKind.FULL:
            return request
        protocol_dimension = ACPProbeDimension(
            profile_id=request.profile_id,
            revision_id=request.revision_id,
            probe_type=ACPProbeKind.PROTOCOL,
            transport=request.transport,
        )
        protocol = self.store.latest_acp_probe(protocol_dimension)
        if protocol is None or protocol.status is not ACPProbeStatus.VERIFIED:
            raise ValueError(
                "a verified protocol probe is required before a full probe"
            )
        evidence = self._protocol_evidence(protocol)
        modes = (
            {
                "availableModes": tuple(mode.id for mode in protocol.agent_modes),
                "currentModeId": protocol.current_agent_mode_id,
            }
            if protocol.agent_modes
            else evidence.get("modes")
        )
        if isinstance(modes, Mapping):
            advertised = modes.get("availableModes", modes.get("available_modes", ()))
            current = modes.get("currentModeId", modes.get("current_mode_id"))
        else:
            advertised, current = modes, None
        advertised_items = advertised if isinstance(advertised, (list, tuple)) else ()
        mode_ids = tuple(
            str(item.get("id"))
            if isinstance(item, Mapping) and item.get("id") is not None
            else str(item)
            for item in advertised_items
        )
        mode = request.agent_mode_id
        if mode is None and mode_ids:
            mode = str(current) if current in mode_ids else mode_ids[0]
        if mode_ids and mode not in mode_ids:
            raise ValueError("ACP mode is not advertised by the latest protocol probe")
        if not mode_ids and mode is not None:
            raise ValueError("ACP mode is not advertised by the latest protocol probe")
        options_value = protocol.config_options or evidence.get("config_options", ())
        options = (
            tuple(item for item in options_value if isinstance(item, Mapping))
            if isinstance(options_value, (list, tuple))
            else ()
        )
        config = dict(request.session_config)
        if not config:
            for item in options:
                ident = _option_id(item)
                if ident is None:
                    continue
                value = next(
                    (
                        item[key]
                        for key in (
                            "currentValue",
                            "current_value",
                            "default",
                            "default_value",
                        )
                        if item.get(key) is not None
                    ),
                    None,
                )
                if value is None:
                    choices = _option_values(item.get("options"))
                    value = choices[0] if choices else None
                if value is not None:
                    config[ident] = cast(JsonValue, value)
        known = {_option_id(item) for item in options}
        unknown = sorted(set(config) - {item for item in known if item is not None})
        if unknown:
            raise ValueError("ACP session configuration contains unadvertised options")
        missing = sorted(
            item for item in known if item is not None and item not in config
        )
        if missing:
            raise ValueError("ACP session configuration is missing advertised options")
        for item in options:
            ident = _option_id(item)
            if ident is None or ident not in config:
                continue
            choices = _option_values(item.get("options"))
            if choices and config[ident] not in choices:
                raise ValueError("ACP session configuration contains an invalid option")
            option_type = str(item.get("type", "")).lower()
            selected = config[ident]
            if option_type in {"boolean", "bool"} and type(selected) is not bool:
                raise ValueError("ACP boolean option must be boolean")
            if option_type in {"integer", "int"} and (type(selected) is not int):
                raise ValueError("ACP integer option must be an integer")
            if option_type in {"number", "float"} and (
                type(selected) not in {int, float}
            ):
                raise ValueError("ACP numeric option must be a number")
            if (
                option_type in {"string", "select", "enum"}
                and type(selected) is not str
                and not choices
            ):
                raise ValueError("ACP string option must be a string")
        return ACPProbeRequest.model_validate(
            {
                **request.model_dump(mode="python"),
                "agent_mode_id": mode,
                "session_config": config,
            }
        )

    def request(self, request: ACPProbeRequest) -> ACPProbeResult:
        with self._lock:
            self._ensure_open()
            self._current_profile_revision(request)
            result = ACPProbeResult.model_validate(
                {
                    **request.model_dump(mode="python", exclude={"timeout_seconds"}),
                    "status": ACPProbeStatus.QUEUED,
                }
            )
            return self.store.save_acp_probe(result)

    def enqueue(self, request: ACPProbeRequest) -> ACPProbeResult:
        """Validate/normalize dimensions, then persist one queued probe.

        The API uses this before scheduling ``run`` so the returned probe ID
        is the same ID consumed by the background lifecycle task.
        """
        return self.request(self._normalize_full_request(request))

    def start(self, request: ACPProbeRequest) -> ACPProbeResult:
        """Queue and execute a probe on the service-owned worker pool."""
        with self._lock:
            self._ensure_open()
            queued = self.enqueue(request)

            def execute() -> None:
                loop = asyncio.new_event_loop()
                task: asyncio.Task[object] | None = None
                try:
                    asyncio.set_event_loop(loop)
                    task = loop.create_task(self.run(request, queued=queued))
                    with self._lock:
                        self._tasks[queued.id] = (loop, task)
                    loop.run_until_complete(task)
                except (asyncio.CancelledError, RuntimeError):
                    # Cancellation is persisted by run() when the task is
                    # interrupted. Runtime shutdown may close the service
                    # before the task reaches its final persistence step.
                    pass
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                    loop.run_until_complete(
                        asyncio.gather(task, return_exceptions=True)
                    ) if task is not None else None
                    loop.close()
                    with self._lock:
                        self._futures.pop(queued.id, None)
                        self._tasks.pop(queued.id, None)

            # Keep submission and registration under one lock: a very fast
            # worker must not finish and pop its entry before assignment, and
            # shutdown cannot slip between persistence and submission.
            future = self._executor.submit(execute)
            self._futures[queued.id] = future
        return queued

    async def run(
        self,
        request: ACPProbeRequest,
        *,
        runner: Runner | None = None,
        queued: ACPProbeResult | None = None,
    ) -> ACPProbeResult:
        self._ensure_open()
        selected = runner if runner is not None else self.runner
        normalized = self._normalize_full_request(request)
        queued = queued or self.request(normalized)
        with self._lock:
            if self._closed:
                return queued
            current = self.store.get_acp_probe(queued.id)
            if current is not None and current.status is ACPProbeStatus.CANCELLED:
                return current
        started = queued.model_copy(
            update={
                "status": ACPProbeStatus.RUNNING,
                "started_at": datetime.now(timezone.utc),
            }
        )
        try:
            with self._lock:
                if self._closed:
                    return queued
                self.store.save_acp_probe(started)
            outcome = await run_acp_probe(
                normalized, selected, probe_id=queued.id, created_at=queued.created_at
            )
        except asyncio.CancelledError:
            cancelled = queued.model_copy(
                update={
                    "status": ACPProbeStatus.CANCELLED,
                    "started_at": started.started_at,
                    "finished_at": datetime.now(timezone.utc),
                    "error": "probe cancelled",
                }
            )
            with self._lock:
                if not self._closed:
                    self.store.save_acp_probe(cancelled)
            raise
        except BaseException:
            failed = queued.model_copy(
                update={
                    "status": ACPProbeStatus.FAILED,
                    "started_at": started.started_at,
                    "finished_at": datetime.now(timezone.utc),
                    "error": "probe interrupted",
                }
            )
            with self._lock:
                if not self._closed:
                    self.store.save_acp_probe(failed)
            raise
        with self._lock:
            if self._closed:
                return outcome
            current = self.store.get_acp_probe(queued.id)
            if current is not None and current.status is ACPProbeStatus.CANCELLED:
                return current
            return self.store.save_acp_probe(outcome)

    def probe(
        self, request: ACPProbeRequest, *, runner: Runner | None = None
    ) -> ACPProbeResult:
        """Synchronous facade; callers inside an event loop must await run()."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(request, runner=runner))
        raise RuntimeError(
            "ACP probe sync facade cannot run inside an event loop; use await run()"
        )

    def history(self, dimension: ACPProbeDimension) -> ACPProbeHistory:
        self._ensure_open()
        return ACPProbeHistory(
            items=self.store.list_acp_probes(dimension, include_inflight=True)
        )

    def latest(self, dimension: ACPProbeDimension) -> ACPProbeResult | None:
        self._ensure_open()
        return self.store.latest_acp_probe(dimension)

    def cancel(
        self, probe_id: str, *, profile_id: str | None = None
    ) -> ACPProbeResult | None:
        self._ensure_open()
        with self._lock:
            current = self.store.get_acp_probe(probe_id)
            # Profile-scoped API callers must establish ownership before any
            # cancellation side effect.  Returning None keeps the endpoint's
            # existing not-found behavior without revealing another profile's
            # probe.
            if current is None or (
                profile_id is not None and current.profile_id != profile_id
            ):
                return None
            future = self._futures.get(probe_id)
            task_info = self._tasks.get(probe_id)
            if future is not None:
                future.cancel()
            if task_info is not None and not task_info[1].done():
                task_info[0].call_soon_threadsafe(task_info[1].cancel)
            if current.status in {
                ACPProbeStatus.VERIFIED,
                ACPProbeStatus.FAILED,
                ACPProbeStatus.TIMED_OUT,
                ACPProbeStatus.CANCELLED,
            }:
                return current
            finished = datetime.now(timezone.utc)
            duration = (
                (finished - current.started_at).total_seconds() * 1000
                if current.started_at
                else None
            )
            return self.store.save_acp_probe(
                current.model_copy(
                    update={
                        "status": ACPProbeStatus.CANCELLED,
                        "finished_at": finished,
                        "duration_ms": duration,
                        "error": "probe cancelled",
                    }
                )
            )


__all__ = ["ACPProbes"]
