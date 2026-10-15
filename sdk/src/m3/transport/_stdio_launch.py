"""Stdio launch-file schema version shared by the writer and the relay.

Kept separate from :mod:`m3.transport.stdio_proxy`: importing that module from
the package would put it in ``sys.modules`` before ``python -m`` executes it.
"""

STDIO_LAUNCH_VERSION = 2
