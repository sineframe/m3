import sys
from pathlib import Path

from m3 import MCPTestKit, StdioServer

HERE = Path(__file__).resolve().parent

server = StdioServer(
    name="shipping",
    command=sys.executable,
    args=(str(HERE / "shipping_server.py"),),
    cwd=str(HERE),
)


def main():
    with MCPTestKit(env={}) as kit, kit.direct(server) as client:
        result = client.call_tool("shipping_quote", {"weight_kg": 2, "zone": "local"})
    assert result.structured_content == {"amount": 9.0, "currency": "USD"}


if __name__ == "__main__":
    main()
