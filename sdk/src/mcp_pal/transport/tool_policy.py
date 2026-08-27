"""Small, transport-safe gate for portable MCP tool policies.

This module is deliberately dependency-light because it is also imported by
the one-shot stdio proxy process.  The gate runs before a request is sent to
an upstream MCP server; callers can therefore safely use it for both HTTP
and stdio captures.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..policy import ToolDescriptor, ToolPolicyEvaluator
from ..types import FullToolPolicy, RestrictiveToolPolicy, ToolPolicy


class ProxyToolPolicy:
    """Evaluate calls for one proxy connection without exposing arguments."""

    def __init__(
        self,
        policy: ToolPolicy | Mapping[str, Any] | None,
        *,
        server: str,
        known_tools: Iterable[str] = (),
        known_servers: Iterable[str] = (),
        known_tools_by_server: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.server = server
        self.policy = _coerce_policy(policy)
        self._unsupported = policy is not None and self.policy is None
        names = tuple(str(value) for value in known_tools if isinstance(value, str))
        servers = tuple(dict.fromkeys((server, *(str(value) for value in known_servers))))
        self._known_names = {
            str(alias): frozenset(str(name) for name in values if isinstance(name, str))
            for alias, values in (known_tools_by_server or {}).items()
        }
        self._inventory_complete = bool(known_tools_by_server) and all(
            alias in self._known_names and bool(self._known_names[alias]) for alias in servers
        )
        if known_tools and known_tools_by_server is None:
            for alias in servers:
                self._known_names[alias] = frozenset(names)
        if known_tools_by_server is not None:
            descriptors = tuple(
                ToolDescriptor(server=alias, name=str(name))
                for alias, values in known_tools_by_server.items()
                for name in values
                if isinstance(name, str)
            )
        else:
            descriptors = tuple(
                ToolDescriptor(server=alias, name=name)
                for alias in servers
                for name in names
            )
        self._descriptors = descriptors
        # Keep the cursor state with the typed JSON-RPC id.  A fresh first
        # page replaces a previous inventory, while continuation pages are
        # additive until the server's paginated listing is complete.
        self._pending_list_ids: dict[tuple[type[object], object], bool] = {}
        self._known_servers = servers

    @property
    def enabled(self) -> bool:
        return self.policy is not None

    def decide(self, tool: object) -> tuple[bool, str]:
        if self.policy is None:
            if self._unsupported:
                return False, "policy_unavailable"
            return True, "policy_not_requested"
        if not isinstance(tool, str) or not tool or len(tool) > 256:
            return False, "tool_identity_invalid"
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in tool):
            return False, "tool_identity_invalid"
        if isinstance(self.policy, RestrictiveToolPolicy) and tool in self.policy.allowed_tools:
            advertised_by = tuple(
                alias for alias, names in self._known_names.items() if tool in names
            )
            if len(advertised_by) > 1 or (
                len(self._known_servers) > 1 and not self._inventory_complete
            ):
                return False, "ambiguous_tool"
        advertised = self._known_names.get(self.server, frozenset())
        if advertised and tool not in advertised:
            return False, "tool_unavailable"
        try:
            descriptor = ToolDescriptor(server=self.server, name=tool)
            known = tuple(
                ToolDescriptor(server=alias, name=name)
                for alias, names in self._known_names.items()
                for name in names
            ) or self._descriptors
            if (descriptor.server, descriptor.name) not in {
                (item.server, item.name) for item in known
            }:
                known = known + (descriptor,)
            evaluator = ToolPolicyEvaluator(known or (descriptor,))
            decision = evaluator.decide(
                self.policy,
                descriptor,
                harness_name="proxy",
                supports_enforcement=True,
            )
        except Exception:
            return False, "policy_unavailable"
        return decision.allowed, decision.reason

    def observe(self, payload: object) -> None:
        """Learn advertised tools from a successful ``tools/list`` response."""

        if not isinstance(payload, dict):
            return
        identifier = payload.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
            return
        key = (type(identifier), identifier)
        continuation = self._pending_list_ids.pop(key, None)
        if continuation is None:
            return
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        names = {
            item["name"]
            for item in result["tools"]
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        # An empty but valid first page is meaningful: it clears stale
        # inventory.  Continuation pages merge into the first page.
        if continuation:
            names = set(self._known_names.get(self.server, ())) | names
        self._known_names[self.server] = frozenset(names)

    def observe_request(self, payload: object) -> None:
        if not isinstance(payload, dict) or payload.get("method") != "tools/list":
            return
        identifier = payload.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
            return
        params = payload.get("params")
        continuation = isinstance(params, Mapping) and params.get("cursor") is not None
        self._pending_list_ids[(type(identifier), identifier)] = continuation

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "ProxyToolPolicy | None":
        policy = payload.get("policy")
        server = payload.get("server")
        if not isinstance(server, str) or not server:
            return None
        return cls(
            policy,
            server=server,
            known_tools=payload.get("known_tools", ()),
            known_servers=payload.get("known_servers", ()),
            known_tools_by_server=payload.get("known_tools_by_server"),
        )


def _coerce_policy(value: ToolPolicy | Mapping[str, Any] | None) -> ToolPolicy | None:
    if value is None:
        return None
    if isinstance(value, (RestrictiveToolPolicy, FullToolPolicy)):
        return value
    if not isinstance(value, Mapping):
        return None
    kind = value.get("kind")
    try:
        if kind == "restrictive":
            return RestrictiveToolPolicy(
                allowed_tools=tuple(value.get("allowed_tools", ())),
                denied_tools=tuple(value.get("denied_tools", ())),
            )
        if kind == "full":
            return FullToolPolicy(acknowledge_risk=value.get("acknowledge_risk") is True)
    except Exception:
        return None
    # Native provider policy cannot be meaningfully enforced by a portable
    # proxy.  The caller records this as unavailable and fails closed.
    return None


__all__ = ["ProxyToolPolicy"]
