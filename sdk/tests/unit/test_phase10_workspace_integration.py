from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from mcp.server.lowlevel import Server
from mcp.types import ListToolsResult

from mcp_pal.agent_session import AsyncAgentSession
from mcp_pal.async_api import AsyncMCPTestKit
from mcp_pal.execution_runtime import AsyncExecutionController
from mcp_pal.harness.contracts import HarnessAdapterCapabilities
from mcp_pal.server_group import ServerGroupManager
from mcp_pal.storage import SQLiteExecutionStore
from mcp_pal.transport.local import current_workspace_root
from mcp_pal.types import (
    ACPAgent,
    AgentSpec,
    ArtifactPolicy,
    InProcessServer,
    ServerBinding,
    StdioServer,
    TextContent,
    TurnResponse,
    UserMessage,
)


def _empty_server() -> Server:
    async def list_tools(_context: object, _params: object) -> ListToolsResult:
        return ListToolsResult(tools=[])

    return Server("workspace-fixture", on_list_tools=list_tools)


@pytest.mark.asyncio
async def test_in_process_direct_factory_receives_scoped_workspace(tmp_path: Path) -> None:
    def factory() -> Server:
        root = current_workspace_root()
        assert root is not None
        Path(root, "inprocess-created.txt").write_text("created")
        return _empty_server()

    async with AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as kit:
        client = kit.direct(
            InProcessServer(name="workspace", factory=factory),
            workspace_root=str(tmp_path),
        )
        async with client:
            assert client.initialization is not None
    assert (tmp_path / "inprocess-created.txt").read_text() == "created"


@pytest.mark.asyncio
@pytest.mark.process_lifecycle
async def test_stdio_direct_defaults_process_cwd_to_scoped_workspace(tmp_path: Path) -> None:
    script = tmp_path / "workspace_stdio.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request.get('method') == 'initialize':\n"
        "        pathlib.Path('stdio-created.txt').write_text('created')\n"
        "        result = {'protocolVersion':'2025-11-25','capabilities':{},'serverInfo':{'name':'stdio','version':'1'}}\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':request.get('id'),'result':result}), flush=True)\n"
        "    elif request.get('method') == 'notifications/initialized':\n"
        "        continue\n"
    )
    async with AsyncMCPTestKit(env={}, cwd=str(tmp_path)) as kit:
        client = kit.direct(
            StdioServer(name="workspace", command=sys.executable, args=(str(script),)),
            workspace_root=str(tmp_path),
        )
        async with client:
            assert client.initialization is not None
    assert (tmp_path / "stdio-created.txt").read_text() == "created"


@pytest.mark.asyncio
async def test_agent_execution_has_one_workspace_event_and_projects_artifacts(tmp_path: Path) -> None:
    class Adapter:
        name = "workspace-agent"
        capabilities = HarnessAdapterCapabilities(name=name)

        async def preflight(self, _launch: object) -> object:
            from mcp_pal.types import Readiness

            return Readiness(ready=True)

        async def start(self, _spec: AgentSpec) -> None:
            return None

        async def open(self, launch: object) -> "Adapter":
            root = Path(str(getattr(launch, "workspace_root")))
            (root / "agent-result.json").write_text('{"ok":true}')
            return self

        async def send(
            self,
            _message: UserMessage,
            *,
            timeout: float | None = None,
            metadata: object | None = None,
        ) -> TurnResponse:
            del timeout, metadata
            return TurnResponse(content=(TextContent(text="ok"),))

        async def close(self) -> None:
            return None

    adapter = Adapter()

    class Kit:
        def agent_session(
            self,
            spec: AgentSpec,
            *,
            _event_sink: object,
            _trace_recorder: object,
            _trace_owner: bool,
            _execution_id: object,
        ) -> AsyncAgentSession:
            del _execution_id
            return AsyncAgentSession(
                spec,
                adapter,
                server_manager=ServerGroupManager(spec.servers),
                event_sink=_event_sink,  # type: ignore[arg-type]
                trace_recorder=_trace_recorder,  # type: ignore[arg-type]
                trace_owner=_trace_owner,
            )

    spec = AgentSpec(
        harness=ACPAgent(model="workspace-agent"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="echo")),),
        message=UserMessage(content=(TextContent(text="run"),)),
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("agent-result.json",),
    )
    handle = AsyncExecutionController(Kit()).submit(spec)
    result = await handle.result(timeout=5)
    assert result.trace is not None
    assert [event.kind.value for event in result.trace.events].count("workspace.changed") == 1
    assert [artifact.name for artifact in result.artifacts] == ["agent-result.json"]
    assert all(artifact.execution_id == result.snapshot.execution_id for artifact in result.artifacts)


@pytest.mark.asyncio
async def test_persistent_agent_workspace_uses_outer_execution_artifact_store_and_id(
    tmp_path: Path,
) -> None:
    """Submitted agent capture must survive the session boundary and reopen."""

    class Adapter:
        async def start(self, _spec: AgentSpec) -> None:
            return None

        async def open(self, launch: object) -> "Adapter":
            root = Path(str(getattr(launch, "workspace_root")))
            (root / "agent-result.txt").write_bytes(b"persistent-agent-artifact")
            return self

        async def send(
            self,
            _message: UserMessage,
            *,
            timeout: float | None = None,
            metadata: object | None = None,
        ) -> TurnResponse:
            del timeout, metadata
            return TurnResponse(content=(TextContent(text="ok"),))

        async def close(self) -> None:
            return None

    adapter = Adapter()
    store = SQLiteExecutionStore(tmp_path / "agent-artifacts.sqlite")
    observed: dict[str, object] = {}

    class Kit:
        def agent_session(
            self,
            spec: AgentSpec,
            *,
            _event_sink: object,
            _trace_recorder: object,
            _trace_owner: bool,
            _execution_id: object,
            _artifact_store: object,
        ) -> AsyncAgentSession:
            observed["artifact_store"] = _artifact_store
            observed["execution_id"] = _execution_id
            return AsyncAgentSession(
                spec,
                adapter,
                event_sink=_event_sink,  # type: ignore[arg-type]
                trace_recorder=_trace_recorder,  # type: ignore[arg-type]
                trace_owner=_trace_owner,
                artifact_store=_artifact_store,  # type: ignore[arg-type]
            )

    spec = AgentSpec(
        harness=ACPAgent(model="persistent-workspace-agent"),
        servers=(ServerBinding(server=StdioServer(name="fixture", command="echo")),),
        message=UserMessage(content=(TextContent(text="run"),)),
        artifact_policy=ArtifactPolicy.ALWAYS,
        declared_artifacts=("agent-result.txt",),
    )
    controller = AsyncExecutionController(Kit(), store=store, worker=False)
    handle = controller.submit(spec)
    await handle._start_from_worker()
    result = await handle.result(timeout=5)

    assert observed["artifact_store"] is store.artifacts
    assert observed["execution_id"] == result.snapshot.execution_id
    assert len(result.artifacts) == 1
    assert result.artifacts[0].execution_id == result.snapshot.execution_id
    expected_hash = hashlib.sha256(b"persistent-agent-artifact").hexdigest()
    assert result.artifacts[0].sha256 == expected_hash

    store.close()
    reopened = SQLiteExecutionStore(tmp_path / "agent-artifacts.sqlite")
    try:
        assert reopened.artifacts.get(result.artifacts[0]) == b"persistent-agent-artifact"
        assert reopened.artifacts.get_ref(result.artifacts[0].artifact_id).sha256 == expected_hash
    finally:
        reopened.close()
