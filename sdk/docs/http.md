# Streamable HTTP

Use `HTTPServer` when the MCP server is already available at an HTTP
endpoint. The concrete server value selects Streamable HTTP; there is no
separate transport argument. Its URL identifies one MCP protocol endpoint, not
a collection of REST routes.

## Test the endpoint directly

Start with a direct contract test. The repository example below targets the
external DeepWiki service, so it is nondeterministic and must be invoked
explicitly. It initializes the endpoint, checks the documented tool subset,
calls `read_wiki_structure`, and checks a non-empty textual result. Keep calls
inside the direct-client context; inspect finalized trace data only after the
client has closed:

```python
from collections.abc import Mapping

from mcp_pal import MCPTestKit
from mcp_pal.types import ExecutionOutcome, HTTPServer, TransportKind

server = HTTPServer(
    name="deepwiki",
    url="https://mcp.deepwiki.com/mcp",
)
documented_tools = {
    "ask_question",
    "read_wiki_contents",
    "read_wiki_structure",
}

with MCPTestKit(env={}) as kit:
    with kit.direct(server) as client:
        assert client.initialization is not None
        tools = client.list_all_tools()
        assert tools
        assert documented_tools <= {tool.name for tool in tools}
        result = client.call_tool(
            "read_wiki_structure",
            {"repoName": "modelcontextprotocol/python-sdk"},
        )
        assert result.is_error is False
        text_blocks = [
            block["text"]
            for block in result.content
            if isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        assert any(text.strip() for text in text_blocks)

    evidence = client.transport_evidence
    assert evidence is not None
    assert evidence.state == "closed"
    trace = client.final_trace
    assert trace is not None
    view = trace.view()
    assert view.outcome is ExecutionOutcome.COMPLETED
    assert any(
        entry.configured.value is TransportKind.STREAMABLE_HTTP
        and entry.instrumented.value is TransportKind.STREAMABLE_HTTP
        for entry in view.transports
    )
```

The complete executable external example is
[`test_streamable_http.py`](../examples/nondeterministic/test_streamable_http.py).
It uses the fixed public DeepWiki endpoint. The endpoint and documented tool
contract are described in the [DeepWiki MCP documentation](https://docs.devin.ai/work-with-devin/deepwiki-mcp).
The OpenCode portions are nondeterministic and may incur provider usage. Invoke
that exact file when you want to run it:

```bash
uv run --env-file .env --project sdk --all-extras \
  pytest -q sdk/examples/nondeterministic/test_streamable_http.py
```

The normal `sdk/examples/tests` command does not collect that file; it contains
the deterministic local examples instead. For a local subprocess alternative,
see the deterministic [`test_quick_start.py`](../examples/tests/test_quick_start.py)
example.

## Connect an endpoint with authentication

Static, non-secret headers can be part of the server value. Resolve a bearer
credential through a `SecretReference`; the resolved secret is sent as a
header, not recorded as a literal in test code:

```python
from mcp_pal import MCPTestKit
from mcp_pal.types import SecretReference, HTTPServer

server = HTTPServer(
    name="catalog",
    url="https://example.test/mcp",
    headers={"X-Client": "catalog-tests"},
)
token = SecretReference(source="environment", name="CATALOG_MCP_TOKEN")

with MCPTestKit(env={}) as kit:
    with kit.direct(server, bearer_token=token) as client:
        result = client.list_all_tools()
        assert result
```

Never put credentials literally in a URL or in a URL query parameter. The
`SecretReference` points to the environment value without embedding that value
in the server definition or assertions.

## Expose the endpoint to an agent

An agent binding makes the endpoint available to a harness under a stable
alias. A public endpoint exposed to an agent requires explicit `PUBLIC` trust:

```python
import os
import shutil

from mcp_pal import MCPTestKit, expect
from mcp_pal.types import (
    AgentSpec,
    OpenCode,
    RestrictiveToolPolicy,
    SecretReference,
    ServerBinding,
    HTTPServer,
    TrustLevel,
)

server = HTTPServer(
    name="deepwiki",
    url="https://mcp.deepwiki.com/mcp",
    trust=TrustLevel.PUBLIC,
)
model = os.environ.get("MCP_PAL_OPENCODE_MODEL", "opencode/big-pickle")
provider = model.split("/", 1)[0] if "/" in model else None
spec = AgentSpec(
    harness=OpenCode(
        model=model,
        provider=provider,
        executable=shutil.which("opencode") or "opencode",
        credential_references={
            "OPENCODE_API_KEY": SecretReference(
                source="environment", name="OPENCODE_API_KEY"
            )
        },
    ),
    servers=(ServerBinding(server=server, alias="deepwiki"),),
    tool_policy=RestrictiveToolPolicy(
        allowed_tools=(
            "deepwiki:ask_question",
            "deepwiki:read_wiki_contents",
            "deepwiki:read_wiki_structure",
        )
    ),
)

with MCPTestKit(env={}) as kit:
    with kit.agent_session(spec) as session:
        turn = session.send(
            "Retrieve the documentation topic hierarchy for "
            "modelcontextprotocol/python-sdk.",
            timeout=120,
        )

expect(session.result).to_have_tool_call(
    "read_wiki_structure",
    turn=turn,
    server="deepwiki",
    arguments={"repoName": "modelcontextprotocol/python-sdk"},
    status="success",
    count=1,
)
```

Keep realistic safe alternatives available when the claim is tool selection.
Exposing only the expected tool proves that the tool can be used; it does not
prove that the agent selected it. The finalized `session.result` is the place
for assertions after the session closes.

## Use a harness matrix for a known tool

`HarnessMatrix.each_tool` describes a server-owned tool and expands it across
harness configurations. It intentionally tests a known call through each
configured harness; it does not test free tool selection:

```python
import os
import shutil

from mcp_pal import MCPTestKit, expect
from mcp_pal.matrix import HarnessCase, HarnessMatrix, ServerCase, ToolCase
from mcp_pal.types import (
    ExecutionOutcome,
    OpenCode,
    SecretReference,
    HTTPServer,
    TrustLevel,
)

model = os.environ.get("MCP_PAL_OPENCODE_MODEL", "opencode/big-pickle")
provider = model.split("/", 1)[0] if "/" in model else None
opencode = OpenCode(
    model=model,
    provider=provider,
    executable=shutil.which("opencode") or "opencode",
    credential_references={
        "OPENCODE_API_KEY": SecretReference(
            source="environment", name="OPENCODE_API_KEY"
        )
    },
)

server = ServerCase(
    name="deepwiki",
    server=HTTPServer(
        name="deepwiki",
        url="https://mcp.deepwiki.com/mcp",
        trust=TrustLevel.PUBLIC,
    ),
    tools=(
        ToolCase(
            name="read_wiki_structure",
            arguments={"repoName": "modelcontextprotocol/python-sdk"},
            prompt=(
                "For modelcontextprotocol/python-sdk, inspect the wiki "
                "structure and report its top-level documentation sections."
            ),
        ),
    ),
)
matrix = HarnessMatrix.each_tool(
    servers=(server,),
    harnesses=(HarnessCase(name="opencode", harness=opencode),),
)
case = matrix.cases()[0]

with MCPTestKit(env={}) as kit:
    result = case.run(kit=kit, timeout=120)

assert result.snapshot.outcome is ExecutionOutcome.COMPLETED, result.error
expect(result).to_have_tool_call(
    "read_wiki_structure",
    server="deepwiki",
    arguments={"repoName": "modelcontextprotocol/python-sdk"},
    status="success",
    count=1,
)
```

## Trust and lifecycle

For a direct connection to a public endpoint, the default `UNTRUSTED` label can
be used. When an agent may reach a public endpoint, set `TrustLevel.PUBLIC` to
record that explicit exposure decision. A private or localhost endpoint that
you own requires `TrustLevel.TRUSTED_PRIVATE`. These labels describe the real
ownership and exposure of the endpoint; they are not validation bypasses.

`MCPTestKit` owns connection and client cleanup and finalizes the trace, but it
does not start or stop a deployed HTTP service. The endpoint must already be
available and keeps running after the test. Keep operations inside the client
or session context and project the finalized trace only after closure.

Streamable HTTP is the current HTTP transport. Use `SSEServer` only when
connecting to an existing legacy HTTP+SSE endpoint. Do not hardcode protocol or
server versions in a test unless the target project explicitly owns that
contract.
