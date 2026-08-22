import json
import sys

from mcp_pal.harness.cli import main


def test_manifest_cli_validate(capsys, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"command": sys.executable, "args": [], "env": {}}))
    assert main(["validate", str(path), "--check-local"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True and output["manifest"]["command"] == sys.executable
