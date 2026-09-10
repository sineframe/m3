"""Explicitly opt-in live Codex and Pi end-to-end coverage.

These tests are intentionally skipped unless the corresponding opt-in flag,
provider model, executable, and one of the explicitly selected credential
routes are present. Codex requires an explicit ``OPENAI_API_KEY``; Pi accepts an explicit
generic provider/credential route, ``OPENAI_API_KEY``/``openai``, or
``PI_CODING_AGENT_DIR``/``openai-codex``. A normal test run therefore cannot start a provider process
or incur a model charge. Each enabled test calls the documented DeepWiki
Streamable HTTP MCP endpoint and asserts two native turns plus the projected
trace.

Run Codex with::

    MCP_PAL_RUN_LIVE_CODEX=1 MCP_PAL_LIVE_CODEX_MODEL=gpt-5-codex \
      uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_live_codex_pi.py -k codex

Run Pi with::

    MCP_PAL_RUN_LIVE_PI=1 MCP_PAL_LIVE_PI_MODEL=gpt-4o \
      uv run --project sdk --all-extras pytest -q sdk/tests/e2e/test_live_codex_pi.py -k pi

Both commands require the installed native executable and an explicit
credential route in the invoking environment. The SDK receives only the
selected value through a ``SecretReference``; it does not copy ambient
credentials into the isolated child.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    AgentSpec,
    Codex,
    HTTPServer,
    Pi,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    TurnOutcome,
    TransportKind,
    TrustLevel,
)


pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.process_lifecycle]

_SDK_ROOT = Path(__file__).parents[2]
_REPOSITORY_ROOT = _SDK_ROOT.parent
_DEEPWIKI_URL = "https://mcp.deepwiki.com/mcp"
_DEEPWIKI_TOOL = "read_wiki_structure"
_DEEPWIKI_ARGUMENTS = {"repoName": "modelcontextprotocol/python-sdk"}


def _server() -> ServerBinding:
    """Return the documented public DeepWiki Streamable HTTP MCP server."""

    return ServerBinding(
        server=HTTPServer(
            name="deepwiki",
            url=_DEEPWIKI_URL,
            trust=TrustLevel.PUBLIC,
        ),
        alias="deepwiki",
    )


def _credential_reference(name: str) -> dict[str, SecretReference]:
    return {name: SecretReference(source="environment", name=name)}


def _codex_spec(
    executable: str, model: str, credential_name: str = "OPENAI_API_KEY"
) -> AgentSpec:
    return AgentSpec(
        harness=Codex(
            model=model,
            executable=executable,
            credential_references=_credential_reference(credential_name),
        ),
        servers=(_server(),),
        tool_policy=RestrictiveToolPolicy(
            allowed_tools=(f"deepwiki:{_DEEPWIKI_TOOL}",)
        ),
    )


def _pi_spec(
    executable: str,
    model: str,
    provider: str = "openai",
    credential_name: str = "OPENAI_API_KEY",
) -> AgentSpec:
    return AgentSpec(
        harness=Pi(
            model=model,
            provider=provider,
            executable=executable,
            credential_references=_credential_reference(credential_name),
        ),
        servers=(_server(),),
        tool_policy=RestrictiveToolPolicy(
            allowed_tools=(f"deepwiki:{_DEEPWIKI_TOOL}",)
        ),
    )


def _codex_route() -> tuple[str, str]:
    """Return the selected Codex credential target and matching model."""

    if os.environ.get("OPENAI_API_KEY"):
        credential = "OPENAI_API_KEY"
        model_name = "MCP_PAL_LIVE_CODEX_MODEL"
    else:
        pytest.skip("set OPENAI_API_KEY explicitly for live Codex")
    model = os.environ.get(model_name) or os.environ.get("MCP_PAL_LIVE_CODEX_MODEL")
    if not model:
        pytest.skip(f"{model_name} is not available for the selected Codex route")
    return credential, model


def _pi_route() -> tuple[str, str, str]:
    """Return Pi's provider, credential target, and matching model."""

    generic_route = (
        os.environ.get("MCP_PAL_LIVE_PI_PROVIDER"),
        os.environ.get("MCP_PAL_LIVE_PI_CREDENTIAL_ENV"),
        os.environ.get("MCP_PAL_LIVE_PI_MODEL"),
    )
    if any(generic_route):
        provider, credential, model = generic_route
        if not provider or not credential or not model:
            pytest.skip(
                "set MCP_PAL_LIVE_PI_PROVIDER, MCP_PAL_LIVE_PI_CREDENTIAL_ENV, "
                "and MCP_PAL_LIVE_PI_MODEL together"
            )
        if not os.environ.get(credential):
            pytest.skip(f"{credential} is not available for the selected Pi route")
        return provider, credential, model
    if os.environ.get("OPENAI_API_KEY"):
        provider = "openai"
        credential = "OPENAI_API_KEY"
        model_name = "MCP_PAL_LIVE_PI_MODEL"
    elif os.environ.get("PI_CODING_AGENT_DIR"):
        provider = "openai-codex"
        credential = "PI_CODING_AGENT_DIR"
        model_name = "MCP_PAL_LIVE_PI_CODEX_MODEL"
    else:
        pytest.skip(
            "set the generic Pi route, OPENAI_API_KEY, or PI_CODING_AGENT_DIR "
            "explicitly for live Pi"
        )
    model = os.environ.get(model_name) or os.environ.get("MCP_PAL_LIVE_PI_MODEL")
    if not model:
        pytest.skip(f"{model_name} is not available for the selected Pi route")
    return provider, credential, model


def _executable(name: str, override_name: str) -> str:
    executable = os.environ.get(override_name) or shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is not installed; set {override_name} to its path")
    return executable


def test_live_native_specs_are_side_effect_free_and_explicit_about_credentials() -> (
    None
):
    codex = _codex_spec("/usr/local/bin/codex", "gpt-5-codex")
    pi = _pi_spec("/usr/local/bin/pi", "gpt-4o")

    assert isinstance(codex.harness, Codex)
    assert isinstance(pi.harness, Pi)
    assert codex.harness.credential_references == _credential_reference(
        "OPENAI_API_KEY"
    )
    assert pi.harness.credential_references == _credential_reference("OPENAI_API_KEY")
    assert isinstance(codex.tool_policy, RestrictiveToolPolicy)
    assert codex.tool_policy.allowed_tools == ("deepwiki:read_wiki_structure",)


@pytest.mark.skipif(
    os.environ.get("MCP_PAL_RUN_LIVE_CODEX") != "1",
    reason="set MCP_PAL_RUN_LIVE_CODEX=1 to call Codex",
)
def test_live_codex_calls_deepwiki_and_records_trace() -> None:
    executable = _executable("codex", "MCP_PAL_CODEX_EXECUTABLE")
    credential, model = _codex_route()
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(_codex_spec(executable, model, credential)) as session:
            first = session.send(
                "Use only the deepwiki read_wiki_structure MCP tool to retrieve "
                "the documentation topic hierarchy for "
                "modelcontextprotocol/python-sdk. Report its top-level sections.",
                timeout=120,
            )
            assert first.error is None, first.error
            assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
            second = session.send(
                "Call read_wiki_structure once more for "
                "modelcontextprotocol/python-sdk and summarize the available "
                "top-level documentation sections.",
                timeout=120,
            )

    assert second.error is None, second.error
    assert first.snapshot.session_id == second.snapshot.session_id
    expect(session.result).to_have_tool_call(
        _DEEPWIKI_TOOL,
        turn=first,
        server="deepwiki",
        status="success",
        count=1,
        arguments=_DEEPWIKI_ARGUMENTS,
    )
    expect(session.result).to_have_tool_call(
        _DEEPWIKI_TOOL,
        turn=second,
        server="deepwiki",
        status="success",
        count=1,
        arguments=_DEEPWIKI_ARGUMENTS,
    )
    assert session.result.trace is not None
    view = session.result.trace.view()
    assert view.runtime.kind == "codex"
    connected = next(
        (entry for entry in view.transports if entry.phase == "connected"), None
    )
    assert connected is not None
    assert connected.configured.value is TransportKind.STREAMABLE_HTTP
    assert connected.instrumented.value is TransportKind.STREAMABLE_HTTP
    calls = [call for call in view.tool_calls if call.tool.value == _DEEPWIKI_TOOL]
    assert len(calls) == 2
    for call in calls:
        assert call.result.state.value == "observed"
        result = call.result.value
        assert result is not None
        assert getattr(result, "content", ())


@pytest.mark.skipif(
    os.environ.get("MCP_PAL_RUN_LIVE_PI") != "1",
    reason="set MCP_PAL_RUN_LIVE_PI=1 to call Pi",
)
def test_live_pi_calls_deepwiki_and_records_trace() -> None:
    executable = _executable("pi", "MCP_PAL_PI_EXECUTABLE")
    provider, credential, model = _pi_route()
    with MCPTestKit(env={}, cwd=str(_REPOSITORY_ROOT)) as kit:
        with kit.agent_session(
            _pi_spec(executable, model, provider, credential)
        ) as session:
            first = session.send(
                "Use only the deepwiki read_wiki_structure MCP tool to retrieve "
                "the documentation topic hierarchy for "
                "modelcontextprotocol/python-sdk. Report its top-level sections.",
                timeout=120,
            )
            assert first.error is None, first.error
            assert first.snapshot.outcome is TurnOutcome.COMPLETED, first.error
            second = session.send(
                "Call read_wiki_structure once more for "
                "modelcontextprotocol/python-sdk and summarize the available "
                "top-level documentation sections.",
                timeout=120,
            )

    assert second.error is None, second.error
    assert first.snapshot.session_id == second.snapshot.session_id
    expect(session.result).to_have_tool_call(
        _DEEPWIKI_TOOL,
        turn=first,
        server="deepwiki",
        status="success",
        count=1,
        arguments=_DEEPWIKI_ARGUMENTS,
    )
    expect(session.result).to_have_tool_call(
        _DEEPWIKI_TOOL,
        turn=second,
        server="deepwiki",
        status="success",
        count=1,
        arguments=_DEEPWIKI_ARGUMENTS,
    )
    assert session.result.trace is not None
    view = session.result.trace.view()
    assert view.runtime.kind == "pi"
    connected = next(
        (entry for entry in view.transports if entry.phase == "connected"), None
    )
    assert connected is not None
    assert connected.configured.value is TransportKind.STREAMABLE_HTTP
    assert connected.instrumented.value is TransportKind.STREAMABLE_HTTP
    calls = [call for call in view.tool_calls if call.tool.value == _DEEPWIKI_TOOL]
    assert len(calls) == 2
    for call in calls:
        assert call.result.state.value == "observed"
        result = call.result.value
        assert result is not None
        assert getattr(result, "content", ())
