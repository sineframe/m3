from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

import pytest
from pydantic import ValidationError

from m3._agent_selection import expand
from m3._types.specs import AgentSpec
from m3.harness.codex import (
    CodexHarnessAdapter,
    codex_configuration,
    render_codex_config,
)
from m3.harness.contracts import HarnessLaunch, HarnessStartupError
from m3.server_group import ServerGroupSnapshot
from m3.types import (
    Codex,
    CodexPermissions,
    RestrictiveToolPolicy,
    ServerBinding,
    StdioServer,
    WorkspaceKind,
    WorkspacePolicy,
)

CODEX_FIXTURE = Path(__file__).parents[1] / "fixtures" / "codex_app_server_fixture.py"

PROFILE = CodexPermissions.profile(
    name="trial",
    extends=":workspace",
    filesystem={
        ":root": "deny",
        ":minimal": "read",
        "/opt/trial-data": "write",
        "~/.cache/uv": "read",
        ":workspace_roots": {".": "write", "**/*.env": "deny"},
    },
    network_enabled=True,
)
READ_ONLY = WorkspacePolicy(kind=WorkspaceKind.READ_ONLY, source="/srv/project")


def _server() -> ServerBinding:
    return ServerBinding(server=StdioServer(name="fixture", command="fixture"))


def _launch(
    permissions: CodexPermissions | None,
    workspace: WorkspacePolicy | None = None,
) -> HarnessLaunch:
    spec = AgentSpec(
        harness=Codex(
            model="fixture", executable=str(CODEX_FIXTURE), permissions=permissions
        ),
        servers=(_server(),),
        **({"workspace": workspace} if workspace is not None else {}),
    )
    return HarnessLaunch(spec, ServerGroupSnapshot(), (), RestrictiveToolPolicy())


class _StartupProcess:
    """Fake App Server answering startup requests with fixed thread/start replies."""

    root: Path | None = None

    def __init__(self, thread_start: dict[str, Any]) -> None:
        self.replies: dict[str, dict[str, Any]] = {
            "initialize": {"result": {}},
            "account/read": {
                "result": {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": True}
            },
            "thread/start": thread_start,
        }
        self.written: list[dict[str, Any]] = []

    async def write(self, payload: dict[str, Any]) -> None:
        self.written.append(payload)

    async def next(self, timeout: float | None = None) -> dict[str, Any]:
        request = next(frame for frame in reversed(self.written) if "id" in frame)
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            **self.replies[request["method"]],
        }

    def thread_start_params(self) -> dict[str, Any]:
        return next(
            frame["params"]
            for frame in self.written
            if frame.get("method") == "thread/start"
        )


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (
            lambda: CodexPermissions(mode="profile", sandbox_mode="read-only"),
            "cannot set sandbox_mode",
        ),
        (lambda: CodexPermissions.profile(), "needs extends or filesystem rules"),
        (
            lambda: CodexPermissions.profile(filesystem={":home": "read"}),
            "unknown Codex special filesystem path",
        ),
        (
            lambda: CodexPermissions.profile(filesystem={"relative/dir": "read"}),
            "must be absolute",
        ),
        (
            lambda: CodexPermissions.profile(filesystem={":root": {".": "read"}}),
            "takes one access value",
        ),
        (
            lambda: CodexPermissions.profile(
                filesystem={":workspace_roots": {"../other": "read"}}
            ),
            "without '.' or '..' components",
        ),
        (
            lambda: CodexPermissions.profile(filesystem={"/srv": {}}),
            "are empty",
        ),
        (
            lambda: CodexPermissions.profile(filesystem={"/srv": "none"}),
            "Input should be",
        ),
        (
            lambda: CodexPermissions.profile(name=":workspace", extends=":workspace"),
            "String should match pattern",
        ),
        (
            lambda: CodexPermissions.profile(extends=":danger-full-access"),  # type: ignore[arg-type]
            "Input should be",
        ),
        (
            lambda: CodexPermissions(
                mode="sandbox", sandbox_mode="read-only", name="x"
            ),
            "cannot set name",
        ),
        (lambda: CodexPermissions(mode="sandbox"), "requires sandbox_mode"),
        (
            lambda: CodexPermissions.sandbox("read-only", network_access=True),
            "only to the workspace-write sandbox",
        ),
        (
            lambda: CodexPermissions.sandbox("danger-full-access"),
            "requires acknowledge_risk=True",
        ),
        (
            lambda: CodexPermissions.sandbox("workspace-write", acknowledge_risk=True),
            "applies only to the danger-full-access sandbox",
        ),
    ],
)
def test_invalid_codex_permissions_are_rejected(build: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        build()


def test_codex_permissions_round_trip_and_unset_permissions_are_omitted() -> None:
    for permissions in (
        PROFILE,
        CodexPermissions.sandbox("workspace-write", network_access=False),
        CodexPermissions.sandbox("danger-full-access", acknowledge_risk=True),
    ):
        harness = Codex(model="m", permissions=permissions)
        assert Codex.model_validate(harness.model_dump(mode="json")) == harness
    assert CodexPermissions.sandbox("read-only").model_dump(mode="json") == {
        "mode": "sandbox",
        "sandbox_mode": "read-only",
    }
    assert "permissions" not in Codex(model="m").model_dump(mode="json")


def test_agent_selection_passes_permissions_to_the_codex_harness() -> None:
    kit = Mock()
    agent = expand(
        kit,
        [
            {
                "harness": "codex",
                "models": ["m"],
                "permissions": {
                    "mode": "sandbox",
                    "sandbox_mode": "workspace-write",
                    "network_access": True,
                },
            }
        ],
    )[0]
    agent.session(server=StdioServer(name="fixture", command="fixture"))
    spec = kit.agent_session.call_args.args[0]
    assert spec.harness.permissions == CodexPermissions.sandbox(
        "workspace-write", network_access=True
    )


@pytest.mark.parametrize(
    ("permissions", "message"),
    [
        (PROFILE, "cannot use a Codex permission profile"),
        (
            CodexPermissions.sandbox("workspace-write"),
            "requires the read-only Codex sandbox, not workspace-write",
        ),
    ],
)
def test_read_only_workspace_rejects_writable_codex_permissions(
    permissions: CodexPermissions, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _launch(permissions, READ_ONLY)
    _launch(CodexPermissions.sandbox("read-only"), READ_ONLY)


@pytest.mark.asyncio
async def test_preflight_rejects_permissions_that_a_resolved_harness_brings_into_a_read_only_workspace() -> (
    None
):
    launch = _launch(CodexPermissions.sandbox("read-only"), READ_ONLY)
    # Profile resolution replaces the harness without re-validating the spec.
    resolved = launch.spec.model_copy(
        update={"harness": Codex(model="fixture", permissions=PROFILE)}
    )
    ready = await CodexHarnessAdapter(executable=str(CODEX_FIXTURE)).preflight(
        HarnessLaunch(resolved, ServerGroupSnapshot(), (), RestrictiveToolPolicy())
    )
    assert not ready.ready
    assert ready.reason == "a read-only workspace cannot use a Codex permission profile"


def test_profile_is_rendered_as_the_default_permissions_profile() -> None:
    launch = _launch(PROFILE)
    rendered = tomllib.loads(render_codex_config(launch))
    assert rendered == codex_configuration(launch)
    assert rendered["default_permissions"] == "trial"
    assert rendered["permissions"] == {
        "trial": {
            "extends": ":workspace",
            "filesystem": {
                ":root": "deny",
                ":minimal": "read",
                "/opt/trial-data": "write",
                "~/.cache/uv": "read",
                ":workspace_roots": {".": "write", "**/*.env": "deny"},
            },
            "network": {"enabled": True},
        }
    }
    assert "sandbox_workspace_write" not in rendered


@pytest.mark.parametrize("network_access", [True, False])
def test_workspace_write_sandbox_renders_its_network_access(
    network_access: bool,
) -> None:
    launch = _launch(
        CodexPermissions.sandbox("workspace-write", network_access=network_access)
    )
    rendered = tomllib.loads(render_codex_config(launch))
    assert rendered == codex_configuration(launch)
    assert rendered["sandbox_workspace_write"] == {"network_access": network_access}
    assert "default_permissions" not in rendered


def test_default_codex_configuration_has_no_permission_settings() -> None:
    rendered = tomllib.loads(render_codex_config(_launch(None)))
    assert set(rendered) == {"features", "mcp_servers"}


@pytest.mark.asyncio
async def test_profile_session_starts_without_sandbox_and_records_the_active_profile() -> (
    None
):
    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    session = await adapter.open(_launch(PROFILE))
    try:
        # The fixture reports the config's profile only for a thread/start
        # without `sandbox`, as Codex does.
        assert adapter._session_metadata["permission_profile"] == "trial"
        assert "sandbox" not in adapter._session_metadata
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permissions", "sandbox"),
    [
        (None, "workspace-write"),
        (CodexPermissions.sandbox("read-only"), "read-only"),
        (
            CodexPermissions.sandbox("workspace-write", network_access=True),
            "workspace-write",
        ),
        (
            CodexPermissions.sandbox("danger-full-access", acknowledge_risk=True),
            "danger-full-access",
        ),
    ],
)
async def test_sandbox_sessions_send_the_requested_sandbox(
    permissions: CodexPermissions | None, sandbox: str
) -> None:
    adapter = CodexHarnessAdapter(executable=str(CODEX_FIXTURE))
    session = await adapter.open(_launch(permissions))
    try:
        assert adapter._session_metadata["sandbox"] == sandbox
        assert "permission_profile" not in adapter._session_metadata
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "reported"),
    [
        (None, "none"),
        ({"id": ":workspace"}, "':workspace'"),
    ],
)
async def test_profile_that_codex_did_not_apply_fails_startup(
    active: dict[str, str] | None, reported: str
) -> None:
    process = _StartupProcess(
        {"result": {"thread": {"id": "t"}, "activePermissionProfile": active}}
    )
    with pytest.raises(HarnessStartupError) as caught:
        await CodexHarnessAdapter().initialize(process, _launch(PROFILE))  # type: ignore[arg-type]
    assert str(caught.value) == (
        f"Codex did not apply permission profile 'trial' (active profile: {reported})"
    )
    assert "sandbox" not in process.thread_start_params()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("permissions", "sandbox", "message"),
    [
        (
            CodexPermissions.sandbox("read-only"),
            {"type": "workspaceWrite", "networkAccess": False},
            "Codex did not start the thread in the read-only sandbox",
        ),
        (
            CodexPermissions.sandbox("workspace-write", network_access=True),
            {"type": "workspaceWrite", "networkAccess": False},
            "Codex did not apply the requested sandbox network access",
        ),
    ],
)
async def test_sandbox_that_codex_did_not_apply_fails_startup(
    permissions: CodexPermissions, sandbox: dict[str, Any], message: str
) -> None:
    process = _StartupProcess({"result": {"thread": {"id": "t"}, "sandbox": sandbox}})
    with pytest.raises(HarnessStartupError, match=message):
        await CodexHarnessAdapter().initialize(process, _launch(permissions))  # type: ignore[arg-type]
    assert process.thread_start_params()["sandbox"] == permissions.sandbox_mode


@pytest.mark.asyncio
async def test_thread_start_error_keeps_the_redacted_reason_when_permissions_are_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-login"))
    error = {
        "code": -32603,
        "message": "error creating thread: fs sandbox helper failed: "
        "sandbox-exec: execvp() of '/opt/codex' failed: Operation not permitted "
        "(ENV-CANARY)",
    }
    reasons = []
    for permissions in (PROFILE, None):
        adapter = CodexHarnessAdapter(environment={"OPENAI_API_KEY": "ENV-CANARY"})
        launch = _launch(permissions)
        root = tmp_path / str(len(reasons))
        root.mkdir()
        adapter.environment_for_launch(launch, root)
        with pytest.raises(HarnessStartupError) as caught:
            await adapter.initialize(_StartupProcess({"error": error}), launch)  # type: ignore[arg-type]
        reasons.append(str(caught.value))
    assert reasons == [
        "Codex thread/start failed (JSON-RPC -32603): error creating thread: fs "
        "sandbox helper failed: sandbox-exec: execvp() of '/opt/codex' failed: "
        "Operation not permitted ([REDACTED])",
        "Codex thread/start failed (JSON-RPC -32603)",
    ]
