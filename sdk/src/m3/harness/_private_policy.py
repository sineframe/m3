"""In-process policy identity kept separate from public native evidence."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from ..trace.redaction import RedactionConfig, redact_for_api


class PrivateToolCalls:
    """Original identities for enforcement; never persist or export this object."""

    __slots__ = ("_calls", "_redaction")

    def __init__(
        self, calls: tuple[Mapping[str, Any], ...], redaction: RedactionConfig
    ) -> None:
        self._calls = tuple(
            MappingProxyType(
                {
                    key: value
                    for key, value in call.items()
                    if key
                    in {
                        "server",
                        "tool",
                        "qualified_name",
                        "call_id",
                        "provenance",
                        "destructive",
                    }
                }
            )
            for call in calls
        )
        self._redaction: RedactionConfig | None = redaction

    @property
    def calls(self) -> tuple[Mapping[str, Any], ...]:
        return self._calls

    def project(self, value: Any) -> Any:
        if self._redaction is None:
            raise RuntimeError("private policy envelope has been released")
        return redact_for_api(value, config=self._redaction)

    def release(self) -> None:
        """Drop original identity and registry references after enforcement."""

        self._calls = ()
        self._redaction = None

    def __repr__(self) -> str:
        return "PrivateToolCalls(<non-serializable>)"

    def __getstate__(self) -> None:
        raise TypeError("private tool identities are not serializable")

    def __reduce__(self) -> Any:
        raise TypeError("private tool identities are not serializable")
