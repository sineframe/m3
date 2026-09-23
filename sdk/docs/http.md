# Streamable HTTP

Agent behavior tests pass an `HTTPServer` directly to `agent.run` or
`agent.session`. Put MCP bearer references in `HTTPServer.headers`; provider
credentials are configured separately with the CLI environment.

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

from m3 import MCPTestKit
from m3.types import ExecutionOutcome, HTTPServer, TransportKind

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
The OpenCode, Codex, and Pi portions are nondeterministic and may incur provider usage. Invoke
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
from m3 import MCPTestKit
from m3.types import SecretReference, HTTPServer

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

An agent can use an HTTP MCP endpoint through the same selected `agent`
fixture as a local server. Mark the test and pass the server explicitly. A
public endpoint exposed to an agent requires `TrustLevel.PUBLIC`:

```python
import pytest
from m3 import expect
from m3.types import HTTPServer, TrustLevel

@pytest.fixture
def deepwiki_server():
    return HTTPServer(
        name="deepwiki",
        url="https://mcp.deepwiki.com/mcp",
        trust=TrustLevel.PUBLIC,
    )

@pytest.mark.m3
def test_agent_reads_wiki_structure(agent, deepwiki_server):
    result = agent.run(
        "Inspect the wiki structure for modelcontextprotocol/python-sdk.",
        server=deepwiki_server,
    )
    expect(result).to_have_tool_call(
        "read_wiki_structure", server="deepwiki", status="success"
    )
```

Set the model provider key in the process environment, or use an explicitly
loaded `.env` file:

```bash
m3 test --env-file .env \
  --harness opencode=opencode/big-pickle -- tests/test_deepwiki.py
```

The OpenCode route above uses `OPENCODE_API_KEY`; Codex uses `OPENAI_API_KEY`
when authenticating with a provider key. A custom source can be mapped by
variable **name** with `--credential-env TARGET=SOURCE`. Keep realistic safe
alternatives available when the claim is which tool the agent chooses.

An MCP endpoint token is separate from the model provider key. Put its
reference on the HTTP server's headers, never in a URL or a CLI value:

```python
from m3.types import HTTPServer, SecretReference, TrustLevel

private_server = HTTPServer(
    name="catalog",
    url="https://catalog.example.com/mcp",
    trust=TrustLevel.TRUSTED_PRIVATE,
    headers={
        "Authorization": SecretReference(
            source="environment", name="CATALOG_MCP_AUTHORIZATION"
        ),
    },
)
```

`CATALOG_MCP_AUTHORIZATION` contains the complete header value in the process
environment. The direct-client `bearer_token=SecretReference(...)` option
remains available when testing the endpoint without an agent.

## Combine an HTTP server with ToolMatrix

A ToolMatrix case can directly call a known HTTP tool, or supply the server and
prompt to a marked agent test. Both forms retain its server alias:

```python
import pytest
from m3 import expect
from m3.matrix import ServerCase, ToolCase, ToolMatrix

matrix = ToolMatrix(servers=(ServerCase(
    name="deepwiki",
    server=deepwiki_server,
    tools=(ToolCase(
        name="read_wiki_structure",
        arguments={"repoName": "modelcontextprotocol/python-sdk"},
        prompt="Read the wiki structure for modelcontextprotocol/python-sdk.",
    ),),
),))

@matrix.parametrize()
def test_http_tool_contract(case):
    assert case.run().direct_result is not None

@pytest.mark.m3
@matrix.parametrize()
def test_agent_chooses_http_tool(case, agent):
    result = agent.run(case.tool.prompt, server=case.server)
    expect(result).to_have_tool_call(
        case.tool.name, server=case.server.name, status="success"
    )
```

CLI-selected harnesses/models and `--trials` combine with each ToolMatrix case
as ordinary pytest parameters. The declared tool does not narrow what the
agent can see.

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

Streamable HTTP is the supported HTTP transport. Do not hardcode protocol or
server versions in a test unless the target project explicitly owns that
contract.
