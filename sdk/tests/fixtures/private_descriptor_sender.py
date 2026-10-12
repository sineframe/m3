"""Transfer a native connection, optionally waiting for the receiver receipt."""

from __future__ import annotations

import array
import socket
import sys

channel = socket.socket(fileno=int(sys.argv[1]))
connected = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connected.connect(sys.argv[2])
channel.sendmsg(
    [b"x"],
    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [connected.fileno()]))],
)
if sys.argv[3] == "ack":
    channel.settimeout(5)
    assert channel.recv(1) == b"a"
connected.close()
channel.close()
