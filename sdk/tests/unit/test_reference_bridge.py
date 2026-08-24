import sys

from mcp_pal.bridge.reference import _rpc, _selected_stdio


def test_noisy_mcp_stderr_cannot_deadlock_reference_bridge(tmp_path):
    server=tmp_path/"noisy.py"
    server.write_text('''
import json,sys
for line in sys.stdin:
 request=json.loads(line)
 sys.stderr.write("x"*200000); sys.stderr.flush()
 print(json.dumps({"jsonrpc":"2.0","id":request["id"],"result":{}}),flush=True)
''')
    process=_selected_stdio({"command":sys.executable,"args":[str(server)]})
    try:
        assert _rpc(process,1,"initialize",{})["result"] == {}
    finally:
        process.terminate(); process.wait(timeout=2)
