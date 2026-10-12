"""Server-side call log used by the raw (no-M3) path.

Under M3 the wire capture is the evidence. When an agent runs without M3,
the only proof that a call reached the server is this log. It is written
only when QUIRK_CALL_LOG names a file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def log_call(server: str, tool: str, arguments: dict[str, Any] | None) -> None:
    path = os.environ.get("QUIRK_CALL_LOG")
    if not path:
        return
    line = json.dumps(
        {
            "server": server,
            "tool": tool,
            "arguments": arguments or {},
            "ts": datetime.now(timezone.utc).isoformat(),
        },
        sort_keys=True,
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
