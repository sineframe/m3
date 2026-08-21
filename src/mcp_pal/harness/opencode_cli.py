"""OpenCode CLI harness adapter."""
import asyncio, json, os, re, shutil, signal, tempfile, threading, time
from pathlib import Path
from typing import Any

from .base import HarnessResult, HarnessRunner, RunSpec
from ..domain.events import normalize_events
from ..domain.validation import selected_server_config

READ_ONLY_TOOLS = ["read", "glob", "grep", "lsp", "webfetch", "websearch"]
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

def _event_key(event_type: str, payload: dict) -> tuple:
    return (event_type,payload.get("session_id"),payload.get("tool_use_id"),payload.get("tool_name"),payload.get("text"),json.dumps(payload.get("step"),sort_keys=True,default=str) if payload.get("step") else None)

def _terminal_complete(normalized: list[tuple[str, dict]]) -> bool:
    """Return true only for a final text step, not an intermediate tool step."""
    complete = False
    text_waiting_for_finish = False
    for event_type, payload in normalized:
        if event_type == "step_start":
            complete = False
            text_waiting_for_finish = False
        elif event_type == "assistant_text":
            text_waiting_for_finish = bool(str(payload.get("text") or "").strip())
            complete = False
        elif event_type == "step_finish":
            # A tool step can finish before the final assistant response.
            complete = text_waiting_for_finish
            text_waiting_for_finish = False
        elif event_type in ("tool_call", "error"):
            complete = False
            text_waiting_for_finish = False
    return complete

def _fresh_recovery(normalized, known, prior=()):
    """Deduplicate replayed calls while retaining a later final step finish."""
    fresh=[]
    for index,item in enumerate(normalized):
        event_type,payload=item; key=_event_key(event_type,payload)
        if key in known:
            previous = list(prior) + normalized[:index]
            if event_type == "step_finish" and previous and previous[-1][0] == "assistant_text":
                fresh.append(item)
            continue
        fresh.append(item)
    return fresh

def _env_refs(value: Any) -> Any:
    if isinstance(value, str):
        return ENV_REF.sub(lambda match: "{env:" + match.group(1) + "}", value)
    if isinstance(value, list): return [_env_refs(item) for item in value]
    if isinstance(value, dict): return {key: _env_refs(item) for key, item in value.items()}
    return value

def opencode_config(mcp_config: dict, server_name: str, tool_mode: str) -> dict:
    source = selected_server_config(mcp_config, server_name)["mcpServers"][server_name]
    if source.get("type", "stdio") == "stdio":
        server = {"type": "local", "command": [source["command"], *source.get("args", [])], "enabled": True}
        if source.get("env"): server["environment"] = _env_refs(source["env"])
    else:
        server = {"type": "remote", "url": source["url"], "enabled": True}
        if source.get("headers"): server["headers"] = _env_refs(source["headers"])
    selected = f"{server_name}_*"
    if tool_mode == "mcp_only":
        tools = {"*": False, selected: True}
        permission = {"*": "deny", selected: "allow"}
    elif tool_mode == "mcp_read_only":
        tools = {"*": False, **{name: True for name in READ_ONLY_TOOLS}, selected: True}
        permission = {"*": "deny", **{name: "allow" for name in READ_ONLY_TOOLS}, selected: "allow"}
    else:
        tools, permission = {"*": True}, {"*": "allow"}
    return {"$schema": "https://opencode.ai/config.json", "mcp": {server_name: server}, "tools": tools, "permission": permission, "plugin": []}

class OpenCodeRunner(HarnessRunner):
    def __init__(self, executable: str = "opencode", api_key: str | None = None, provider_api_keys: dict[str, str] | None = None):
        self.executable = executable; self.api_key = api_key; self.provider_api_keys = dict(provider_api_keys or {}); self.process = None; self.cancel_requested = threading.Event()

    def request_cancel(self):
        self.cancel_requested.set(); self._terminate()

    def build_command(self, spec: RunSpec) -> list[str]:
        command = [self.executable, "--pure", "run", "--format", "json", "--thinking", "--model", spec.model]
        if spec.tool_mode == "full": command.append("--auto")
        return command

    async def _export_completed_session(self, session_id: str, environment: dict, cwd: str) -> list[dict] | None:
        """Recover output when OpenCode persists completion but its JSON stream hangs."""
        process = None
        try:
            process=await asyncio.create_subprocess_exec(self.executable,"export",session_id,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=environment,cwd=cwd,start_new_session=True)
            communicate=asyncio.create_task(process.communicate())
            try:
                stdout,_=await asyncio.wait_for(asyncio.shield(communicate),timeout=5)
            except asyncio.TimeoutError:
                await self._terminate_process(process)
                communicate.cancel()
                await asyncio.gather(communicate,return_exceptions=True)
                return None
            if process.returncode != 0: return None
            exported=json.loads(stdout)
        except (OSError,json.JSONDecodeError): return None
        messages=exported.get("messages",[]) if isinstance(exported,dict) else []
        assistant=[message for message in messages if message.get("info",{}).get("role") == "assistant"]
        if not assistant: return None
        last_parts=assistant[-1].get("parts",[])
        if not last_parts or last_parts[-1].get("type") != "step-finish": return None
        if not any(part.get("type") == "text" and part.get("text") for message in assistant for part in message.get("parts",[])): return None
        recovered=[]
        names={"step-start":"step_start","step-finish":"step_finish","text":"text","reasoning":"reasoning","tool":"tool_use"}
        for message in assistant:
            for part in message.get("parts",[]):
                typ=names.get(part.get("type"))
                if typ and (typ != "tool_use" or part.get("state",{}).get("status") in ("completed","error")):
                    recovered.append({"type":typ,"timestamp":part.get("time",{}).get("end") or part.get("time",{}).get("start"),"sessionID":session_id,"part":part,"recovered_from_session":True})
        normalized=[event for raw in recovered for event in normalize_events(raw) ]
        return recovered if _terminal_complete(normalized) else None

    async def _delete_session(self, session_id: str, environment: dict, cwd: str):
        """Remove the persisted OpenCode transcript after the backend has captured it."""
        process = None
        try:
            process=await asyncio.create_subprocess_exec(self.executable,"session","delete",session_id,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,env=environment,cwd=cwd,start_new_session=True)
            communicate=asyncio.create_task(process.communicate())
            try:
                await asyncio.wait_for(asyncio.shield(communicate),timeout=1)
            except asyncio.TimeoutError:
                await self._terminate_process(process)
                communicate.cancel()
                await asyncio.gather(communicate,return_exceptions=True)
        except OSError:
            return

    async def _terminate_process(self, process):
        if process.returncode is not None: return
        try: os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError: pass
        try: await asyncio.wait_for(process.wait(),timeout=1)
        except asyncio.TimeoutError:
            try: os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
            await process.wait()

    async def run(self, spec: RunSpec, on_event=None, cancel_event=None) -> HarnessResult:
        result = HarnessResult(status="running")
        with tempfile.TemporaryDirectory(prefix="mcp-pal-opencode-") as td:
            config_path = os.path.join(td, "opencode.json")
            Path(config_path).write_text(json.dumps(opencode_config(spec.mcp_config, spec.enabled_server, spec.tool_mode)), encoding="utf-8")
            os.chmod(config_path, 0o600)
            environment = os.environ.copy(); environment["OPENCODE_CONFIG"] = config_path; environment["PWD"] = td
            environment["XDG_CONFIG_HOME"] = os.path.join(td,"xdg-config")
            environment["OPENCODE_CONFIG_DIR"] = os.path.join(td,"opencode-config")
            # Keep provider credentials discoverable while preventing global HOME
            # and project config files from changing the selected tool policy.
            original_home = environment.get("HOME") or os.path.expanduser("~")
            environment["HOME"] = td
            original_data = environment.get("XDG_DATA_HOME") or os.path.join(original_home,".local","share")
            run_data = os.path.join(td,"xdg-data")
            environment["XDG_DATA_HOME"] = run_data
            environment["XDG_STATE_HOME"] = os.path.join(td,"xdg-state")
            environment["XDG_CACHE_HOME"] = os.path.join(td,"xdg-cache")
            auth_source = os.path.join(original_data,"opencode","auth.json")
            auth_target = os.path.join(run_data,"opencode","auth.json")
            if os.path.isfile(auth_source):
                try:
                    os.makedirs(os.path.dirname(auth_target),mode=0o700,exist_ok=True)
                    shutil.copyfile(auth_source,auth_target)
                    os.chmod(auth_target,0o600)
                except OSError:
                    pass
            if self.api_key: environment["OPENCODE_API_KEY"] = self.api_key
            for provider,key in self.provider_api_keys.items():
                normalized=provider.lower().replace("-", "_").upper()
                variable="OPENCODE_API_KEY" if provider.lower() in ("opencode", "opencode-go") else f"{normalized}_API_KEY"
                environment[variable]=key
            try:
                self.process = await asyncio.create_subprocess_exec(*self.build_command(spec), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=td, env=environment, start_new_session=True)
                self.process.stdin.write(spec.prompt.encode()); await self.process.stdin.drain(); self.process.stdin.close()
                async def read_stdout():
                    async for line in self.process.stdout:
                        rawline=line.decode(errors="replace").rstrip("\n")
                        try: raw=json.loads(rawline)
                        except Exception: raw=rawline
                        result.events.append(raw); normalized=normalize_events(raw,spec.enabled_server); result.normalized.extend(normalized)
                        for event_type,payload in normalized:
                            if event_type == "assistant_text": result.final_text += str(payload.get("text") or "")
                            if event_type == "step_finish":
                                result.turns=(result.turns or 0)+1
                                cost=payload.get("cost_usd")
                                if isinstance(cost,(int,float)): result.cost_usd=(result.cost_usd or 0)+cost
                            if on_event:
                                value=on_event(raw,event_type,payload)
                                if asyncio.iscoroutine(value): await value
                        if isinstance(raw,dict): result.session_id=result.session_id or raw.get("sessionID")
                stdout_task=asyncio.create_task(read_stdout()); stderr_task=asyncio.create_task(self.process.stderr.read()); wait_task=asyncio.create_task(self.process.wait()); cancel_task=asyncio.create_task(cancel_event.wait()) if cancel_event else None
                try:
                    deadline=time.monotonic()+spec.timeout_seconds; next_recovery=time.monotonic()+3
                    while not wait_task.done():
                        if self.cancel_requested.is_set() or (cancel_task and cancel_task.done()): result.status="cancelled"; await self._terminate_and_reap(); break
                        remaining=deadline-time.monotonic()
                        if remaining <= 0: result.status="timed_out"; await self._terminate_and_reap(); break
                        if result.session_id and time.monotonic() >= next_recovery:
                            exported=await self._export_completed_session(result.session_id,environment,td); next_recovery=time.monotonic()+2
                            if exported:
                                known={_event_key(event_type,payload) for event_type,payload in result.normalized}
                                for raw in exported:
                                    normalized=normalize_events(raw,spec.enabled_server); fresh=_fresh_recovery(normalized,known,result.normalized)
                                    if not fresh: continue
                                    result.events.append(raw); result.normalized.extend(fresh)
                                    for event_type,payload in fresh:
                                        known.add(_event_key(event_type,payload))
                                        if on_event:
                                            value=on_event(raw,event_type,payload)
                                            if asyncio.iscoroutine(value): await value
                                # Rebuild aggregate metadata from the authoritative export.
                                texts=[payload.get("text","") for event_type,payload in result.normalized if event_type == "assistant_text"]
                                result.final_text="".join(str(value) for value in texts); finishes=[payload for event_type,payload in result.normalized if event_type == "step_finish"]
                                result.turns=len(finishes); costs=[p.get("cost_usd") for p in finishes if isinstance(p.get("cost_usd"),(int,float))]; result.cost_usd=sum(costs) if costs else result.cost_usd
                                if _terminal_complete(result.normalized):
                                    result.status="completed"; await self._terminate_and_reap(); break
                        try: await asyncio.wait_for(asyncio.shield(wait_task),timeout=min(.1,remaining))
                        except asyncio.TimeoutError: continue
                    if wait_task.done() and result.status == "running": result.exit_code=self.process.returncode
                except asyncio.CancelledError:
                    result.status="cancelled"; await self._terminate_and_reap(); raise
                finally:
                    if cancel_task: cancel_task.cancel()
                await asyncio.gather(wait_task,stdout_task,return_exceptions=True)
                result.stderr=(await stderr_task).decode(errors="replace"); result.exit_code=self.process.returncode
                # A clean exit is not enough: older OpenCode releases can exit
                # before flushing the final JSON parts. Try the persisted export
                # once more before classifying the result.
                complete_stream = _terminal_complete(result.normalized)
                if result.status == "running" and result.exit_code == 0 and not complete_stream and result.session_id:
                    exported=await self._export_completed_session(result.session_id,environment,td)
                    if exported:
                        known={_event_key(event_type,payload) for event_type,payload in result.normalized}
                        for raw in exported:
                            normalized=normalize_events(raw,spec.enabled_server); fresh=_fresh_recovery(normalized,known,result.normalized)
                            if not fresh: continue
                            result.events.append(raw); result.normalized.extend(fresh)
                            for event_type,payload in fresh:
                                known.add(_event_key(event_type,payload))
                                if event_type == "assistant_text": result.final_text += str(payload.get("text") or "")
                                if event_type == "step_finish":
                                    result.turns=(result.turns or 0)+1
                                    cost=payload.get("cost_usd")
                                    if isinstance(cost,(int,float)): result.cost_usd=(result.cost_usd or 0)+cost
                                if on_event:
                                    value=on_event(raw,event_type,payload)
                                    if asyncio.iscoroutine(value): await value
                        complete_stream = _terminal_complete(result.normalized)
                if result.status == "running":
                    result.status="completed" if result.exit_code == 0 and complete_stream else "failed"
                    if result.status == "failed" and result.exit_code == 0 and not complete_stream:
                        result.error="OpenCode exited before emitting a complete JSON trace"
                if result.status != "completed" and result.exit_code not in (0,None): result.error=f"OpenCode exited with code {result.exit_code}"
            except FileNotFoundError as error: result.status,result.error="failed",str(error)
            except asyncio.CancelledError: raise
            except Exception as error:
                result.status,result.error="failed",str(error)
                if self.process and self.process.returncode is None: await self._terminate_and_reap()
            finally:
                if result.session_id:
                    await self._delete_session(result.session_id,environment,td)
        return result

    async def _terminate_and_reap(self):
        if self.process and self.process.returncode is None:
            try: os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: await asyncio.wait_for(self.process.wait(),timeout=2)
            except asyncio.TimeoutError:
                try: os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                await self.process.wait()

    def _terminate(self):
        if self.process and self.process.returncode is None:
            try: os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
