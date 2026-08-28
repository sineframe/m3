"""Application ACP probes over the SDK contract and real ACP adapter probes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Protocol, cast

from mcp_pal.harness.acp import full_probe, protocol_probe
from mcp_pal.services.acp_probes import (
    ACPProbeDimension,
    ACPProbeHistory,
    ACPProbeKind,
    ACPProbeRequest,
    ACPProbeResult,
    ACPProbeStatus,
    ACPProbeStore,
    ACPAgentMode,
    JsonValue,
    Runner,
    run_acp_probe,
)


class _ProfileStore(ACPProbeStore, Protocol):
    def get_profile(self, profile_id: str) -> Any: ...
    def get_revision(self, revision_id: str) -> Any: ...


def _option_id(option: Mapping[str, object]) -> str | None:
    value = option.get("id", option.get("configId"))
    return str(value) if value is not None else None


def _option_values(value: object) -> tuple[object, ...]:
    if isinstance(value, (list, tuple)):
        output: list[object] = []
        for item in value:
            if isinstance(item, Mapping) and "value" in item:
                output.append(item["value"])
            elif isinstance(item, Mapping) and isinstance(item.get("options"), (list, tuple)):
                output.extend(_option_values(item["options"]))
            else:
                output.append(item)
        return tuple(output)
    return ()


class ACPProbeService:
    """Persist ACP probe lifecycle and expose exact-dimension history."""

    def __init__(self, store: ACPProbeStore, runner: Runner | None = None) -> None:
        self.store = store
        self.runner = runner if runner is not None else self._run_current_manifest
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ACP probe service is closed")

    def close(self) -> None:
        self._closed = True

    def _current_profile_revision(self, request: ACPProbeRequest) -> tuple[Any, Any]:
        store = cast(_ProfileStore, self.store)
        profile = store.get_profile(request.profile_id)
        revision = store.get_revision(request.revision_id)
        current = getattr(profile, "current_revision_id", None)
        current_id = str(getattr(current, "root", current)) if current is not None else None
        if profile is None or getattr(profile, "kind", None) != "harness":
            raise ValueError("ACP harness profile does not exist")
        if getattr(profile, "archived", False):
            raise ValueError("ACP harness profile is archived")
        if revision is None or getattr(revision, "profile_id", None) != request.profile_id:
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

    async def _run_current_manifest(self, request: ACPProbeRequest) -> Mapping[str, Any]:
        _, revision = self._current_profile_revision(request)
        manifest = self._manifest(revision)
        if request.probe_type is ACPProbeKind.PROTOCOL:
            raw = await protocol_probe(manifest)
        else:
            raw = await full_probe(
                manifest,
                mode_id=request.agent_mode_id,
                session_config=dict(request.session_config),
                transport=request.transport,
            )
        # protocol_probe calls the field ``agent_info`` while full_probe uses
        # ``agent_identity``.  Explicitly lift both plus capabilities/options,
        # and put every other adapter observation under bounded ``evidence``;
        # run_acp_probe intentionally ignores unknown top-level runner keys.
        reserved = {"status", "agent_identity", "agent_info", "agent_capabilities", "config_options", "evidence", "diagnostics", "error"}
        evidence = dict(raw.get("evidence", {})) if isinstance(raw.get("evidence"), Mapping) else {}
        evidence.update({str(key): value for key, value in raw.items() if key not in reserved})
        frame_results: list[Mapping[str, Any]] = []
        frames = raw.get("frames", ())
        if isinstance(frames, (list, tuple)):
            for frame in frames:
                if not isinstance(frame, Mapping):
                    continue
                payload = frame.get("payload")
                response = payload.get("result") if isinstance(payload, Mapping) else None
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
                if response.get("agentInfo") is not None or response.get("agent_info") is not None
            ),
            None,
        )
        raw_identity = raw.get("agent_identity") or raw.get("agent_info") or wire_identity
        raw_modes = raw.get("modes") or wire_session.get("modes", ())
        if isinstance(raw_modes, Mapping):
            advertised = raw_modes.get("availableModes", raw_modes.get("available_modes", ()))
            current = raw_modes.get("currentModeId", raw_modes.get("current_mode_id"))
        else:
            advertised, current = raw_modes, None
        raw_config_options = raw.get("config_options", raw.get("configOptions", ())) or wire_session.get("configOptions", wire_session.get("config_options", ()))
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
                ACPAgentMode.model_validate({"id": item.get("id"), "name": item.get("name", item.get("id"))})
                for item in (advertised if isinstance(advertised, (list, tuple)) else ())
                if isinstance(item, Mapping) and item.get("id") is not None
            ),
            "current_agent_mode_id": str(current) if isinstance(current, (str, int, float)) else None,
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
            raise ValueError("a verified protocol probe is required before a full probe")
        evidence = self._protocol_evidence(protocol)
        modes = (
            {"availableModes": tuple(mode.id for mode in protocol.agent_modes), "currentModeId": protocol.current_agent_mode_id}
            if protocol.agent_modes else evidence.get("modes")
        )
        if isinstance(modes, Mapping):
            advertised = modes.get("availableModes", modes.get("available_modes", ()))
            current = modes.get("currentModeId", modes.get("current_mode_id"))
        else:
            advertised, current = modes, None
        advertised_items = advertised if isinstance(advertised, (list, tuple)) else ()
        mode_ids = tuple(
            str(item.get("id")) if isinstance(item, Mapping) and item.get("id") is not None else str(item)
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
        options = tuple(item for item in options_value if isinstance(item, Mapping)) if isinstance(options_value, (list, tuple)) else ()
        config = dict(request.session_config)
        if not config:
            for item in options:
                ident = _option_id(item)
                if ident is None:
                    continue
                value = next((item[key] for key in ("currentValue", "current_value", "default", "default_value") if item.get(key) is not None), None)
                if value is None:
                    choices = _option_values(item.get("options"))
                    value = choices[0] if choices else None
                if value is not None:
                    config[ident] = cast(JsonValue, value)
        known = {_option_id(item) for item in options}
        unknown = sorted(set(config) - {item for item in known if item is not None})
        if unknown:
            raise ValueError("ACP session configuration contains unadvertised options")
        missing = sorted(item for item in known if item is not None and item not in config)
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
            if option_type in {"number", "float"} and (type(selected) not in {int, float}):
                raise ValueError("ACP numeric option must be a number")
            if option_type in {"string", "select", "enum"} and type(selected) is not str and not choices:
                raise ValueError("ACP string option must be a string")
        return ACPProbeRequest.model_validate({**request.model_dump(mode="python"), "agent_mode_id": mode, "session_config": config})

    def request(self, request: ACPProbeRequest) -> ACPProbeResult:
        self._ensure_open()
        self._current_profile_revision(request)
        result = ACPProbeResult.model_validate({**request.model_dump(mode="python", exclude={"timeout_seconds"}), "status": ACPProbeStatus.QUEUED})
        return self.store.save_acp_probe(result)

    async def run(self, request: ACPProbeRequest, *, runner: Runner | None = None) -> ACPProbeResult:
        self._ensure_open()
        selected = runner if runner is not None else self.runner
        normalized = self._normalize_full_request(request)
        queued = self.request(normalized)
        started = queued.model_copy(update={"status": ACPProbeStatus.RUNNING, "started_at": datetime.now(timezone.utc)})
        try:
            self.store.save_acp_probe(started)
            outcome = await run_acp_probe(normalized, selected, probe_id=queued.id, created_at=queued.created_at)
        except asyncio.CancelledError:
            cancelled = queued.model_copy(update={"status": ACPProbeStatus.CANCELLED, "started_at": started.started_at, "finished_at": datetime.now(timezone.utc), "error": "probe cancelled"})
            self.store.save_acp_probe(cancelled)
            raise
        except BaseException:
            failed = queued.model_copy(update={"status": ACPProbeStatus.FAILED, "started_at": started.started_at, "finished_at": datetime.now(timezone.utc), "error": "probe interrupted"})
            self.store.save_acp_probe(failed)
            raise
        current = self.store.get_acp_probe(queued.id)
        if current is not None and current.status is ACPProbeStatus.CANCELLED:
            return current
        return self.store.save_acp_probe(outcome)

    def probe(self, request: ACPProbeRequest, *, runner: Runner | None = None) -> ACPProbeResult:
        """Synchronous facade; callers inside an event loop must await run()."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run(request, runner=runner))
        raise RuntimeError("ACP probe sync facade cannot run inside an event loop; use await run()")

    def history(self, dimension: ACPProbeDimension) -> ACPProbeHistory:
        self._ensure_open()
        return ACPProbeHistory(items=self.store.list_acp_probes(dimension, include_inflight=True))

    def latest(self, dimension: ACPProbeDimension) -> ACPProbeResult | None:
        self._ensure_open()
        return self.store.latest_acp_probe(dimension)

    def cancel(self, probe_id: str) -> ACPProbeResult | None:
        self._ensure_open()
        current = self.store.get_acp_probe(probe_id)
        if current is None or current.status in {ACPProbeStatus.VERIFIED, ACPProbeStatus.FAILED, ACPProbeStatus.TIMED_OUT, ACPProbeStatus.CANCELLED}:
            return current
        finished = datetime.now(timezone.utc)
        duration = (finished - current.started_at).total_seconds() * 1000 if current.started_at else None
        return self.store.save_acp_probe(current.model_copy(update={"status": ACPProbeStatus.CANCELLED, "finished_at": finished, "duration_ms": duration, "error": "probe cancelled"}))


__all__ = ["ACPProbeService"]
