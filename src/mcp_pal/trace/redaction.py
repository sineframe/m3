"""Best-effort secret removal before trace data reaches persistent storage."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
SENSITIVE_KEY = re.compile(
    r"(^|[-_])(authorization|proxy_authorization|cookie|set_cookie|token|api_key|apikey|secret|password|passwd|credential|private_key)([-_]|$)",
    re.IGNORECASE,
)
SENSITIVE_QUERY = re.compile(r"token|key|secret|password|signature|credential", re.IGNORECASE)


def known_secret_values() -> set[str]:
    """Return only values from environment variables whose names imply secrets."""
    return {
        value
        for key, value in os.environ.items()
        if value and len(value) >= 6 and SENSITIVE_KEY.search(key)
    }


def _redact_url(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        return value
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{hostname}{port}"
        query = urlencode(
            [(key, REDACTED if SENSITIVE_QUERY.search(key) else val) for key, val in parse_qsl(parts.query, keep_blank_values=True)]
        )
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except (TypeError, ValueError):
        return value


def redact(value: Any, *, secrets: set[str] | None = None, path: str = "$") -> tuple[Any, list[str]]:
    """Recursively redact credential-shaped values and return affected paths."""
    secrets = known_secret_values() if secrets is None else secrets
    paths: list[str] = []

    def walk(item: Any, current: str, key_hint: str | None = None) -> Any:
        if key_hint and SENSITIVE_KEY.search(key_hint):
            paths.append(current)
            return REDACTED
        if isinstance(item, dict):
            return {str(key): walk(val, f"{current}.{key}", str(key)) for key, val in item.items()}
        if isinstance(item, list):
            return [walk(val, f"{current}[{index}]") for index, val in enumerate(item)]
        if isinstance(item, tuple):
            return [walk(val, f"{current}[{index}]") for index, val in enumerate(item)]
        if isinstance(item, str):
            redacted_url = _redact_url(item)
            if redacted_url != item:
                paths.append(current)
                item = redacted_url
            for secret in secrets:
                if secret in item:
                    item = item.replace(secret, REDACTED)
                    if current not in paths:
                        paths.append(current)
            return item
        return item

    return walk(value, path), paths
