"""Transparent stdio MCP relay that records newline-delimited JSON-RPC frames."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading

from mcp_pal.trace.capture import CaptureWriter, parse_json_payload


def _relay(source, destination, writer: CaptureWriter, direction: str) -> None:
    try:
        while True:
            line = source.readline()
            if not line:
                break
            writer.write(transport="stdio", direction=direction, payload=parse_json_payload(line))
            destination.write(line)
            destination.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            destination.close()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True)
    parser.add_argument("--baseline", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("server command is required after --")

    writer = CaptureWriter(args.capture, args.baseline)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        shell=False,
        bufsize=0,
    )
    assert process.stdin is not None and process.stdout is not None
    inbound = threading.Thread(
        target=_relay,
        args=(sys.stdin.buffer, process.stdin, writer, "client_to_server"),
        daemon=True,
    )
    outbound = threading.Thread(
        target=_relay,
        args=(process.stdout, sys.stdout.buffer, writer, "server_to_client"),
        daemon=True,
    )
    inbound.start()
    outbound.start()
    return_code = process.wait()
    outbound.join(timeout=1)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
