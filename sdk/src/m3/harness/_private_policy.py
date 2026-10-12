"""In-process policy identity kept separate from public native evidence."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from ..trace.redaction import RedactionConfig, RedactionError, redact_for_api


class PrivateToolCalls:
    """Original identities for enforcement; never persist or export this object."""

    __slots__ = ("_calls", "_redaction")

    def __init__(
        self, calls: tuple[Mapping[str, Any], ...], redaction: RedactionConfig | None
    ) -> None:
        self._calls = tuple(
            MappingProxyType(
                {
                    key: value
                    for key, value in call.items()
                    if key
                    in {
                        "server",
                        "server_name",
                        "tool",
                        "tool_name",
                        "name",
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
            raise RedactionError("private policy projection registry is unavailable")
        return redact_for_api(value, config=self._redaction)

    def release(self) -> None:
        """Drop original identity and registry references after enforcement."""

        self._calls = ()
        self._redaction = None

    def project_policy_violation(self, violation: Mapping[str, Any]) -> dict[str, Any]:
        """Preserve only recognized framework reasons when identities are withheld."""

        reason = violation.get("reason")
        trusted_reason = (
            reason
            if isinstance(reason, str)
            and reason
            in {
                "tool_call_invalid",
                "ambiguous_tool",
                "tool_unavailable",
                "tool_identity_unavailable",
                "tool_identity_invalid",
                "server_unavailable",
                "policy_unavailable",
                "tool denied by restrictive policy",
                "destructive tool requires confirmation",
                "destructive tool confirmation denied",
                "policy_evidence_unavailable",
            }
            else "policy_evidence_unavailable"
        )
        try:
            safe = {key: self.project(value) for key, value in violation.items()}
        except RedactionError:
            safe = {"server": "unavailable", "tool": "unavailable"}
        safe["reason"] = trusted_reason
        return safe

    def __repr__(self) -> str:
        return "PrivateToolCalls(<non-serializable>)"

    def __getstate__(self) -> None:
        raise TypeError("private tool identities are not serializable")

    def __reduce__(self) -> Any:
        raise TypeError("private tool identities are not serializable")
