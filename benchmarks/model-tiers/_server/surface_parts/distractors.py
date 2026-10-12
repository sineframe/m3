"""~42 plausible-but-irrelevant REST tools for the naive catalog. They never touch issue state."""

from __future__ import annotations

from typing import Any

from _backend.store import DomainError

# (name, short description, good description core, returns, params, is_write)
# params: (name, kind, required) kind: s=string i=integer b=boolean
_P_ID = [("id", "s", True)]
_P_PAGE = [("page", "i", False)]
_AREA = {
    "webhooks": ("webhook", "WH"),
    "admin": ("admin", "AD"),
    "exports": ("export", "EX"),
    "audit_log": ("audit log", "AL"),
    "sla_policies": ("SLA policy", "SLA"),
    "teams": ("team", "TM"),
    "integrations": ("integration", "IN"),
    "notifications": ("notification", "NT"),
    "issues": ("issue", "IS"),
    "labels": ("label", "LB"),
    "cycles": ("cycle", "CY"),
    "projects": ("project", "PR"),
    "reports": ("report", "RP"),
}
TEAM_NAMES = ("Platform", "Growth", "Payments", "Mobile", "Web")

SPECS: list[tuple[str, str, str, str, list, bool]] = [
    (
        "webhooks_list",
        "List webhooks",
        "List the outgoing webhook subscriptions configured for the workspace.",
        "{data:[{id,url,events,active}]}",
        _P_PAGE,
        False,
    ),
    (
        "webhooks_get",
        "Get webhook",
        "Fetch one webhook subscription by id.",
        "{data:{id,url,events,active}}",
        _P_ID,
        False,
    ),
    (
        "webhooks_create",
        "Create webhook",
        "Register a new outgoing webhook subscription.",
        "{data:{id,accepted}}",
        [("url", "s", True), ("events", "s", False)],
        True,
    ),
    (
        "webhooks_delete",
        "Delete webhook",
        "Remove a webhook subscription.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "webhooks_test",
        "Test webhook",
        "Send a test ping to a webhook endpoint.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "admin_users_list",
        "List admin users",
        "List workspace members with their admin-console account state.",
        "{data:[{id,display_name,email,active,last_login}]}",
        _P_PAGE,
        False,
    ),
    (
        "admin_roles_list",
        "List roles",
        "List the permission roles defined in the workspace.",
        "{data:[{id,name,permissions}]}",
        _P_PAGE,
        False,
    ),
    (
        "admin_settings_get",
        "Get settings",
        "Read workspace-wide administrative settings.",
        "{data:{workspace,locale,week_start}}",
        [],
        False,
    ),
    (
        "admin_settings_update",
        "Update settings",
        "Change workspace-wide administrative settings.",
        "{data:{id,accepted}}",
        [("locale", "s", False)],
        True,
    ),
    (
        "admin_api_keys_list",
        "List API keys",
        "List API keys issued for the workspace (secrets are masked).",
        "{data:[{id,name,created}]}",
        _P_PAGE,
        False,
    ),
    (
        "admin_api_keys_create",
        "Create API key",
        "Issue a new workspace API key.",
        "{data:{id,accepted}}",
        [("name", "s", True)],
        True,
    ),
    (
        "exports_list",
        "List exports",
        "List previously requested data exports.",
        "{data:[{id,format,state}]}",
        _P_PAGE,
        False,
    ),
    (
        "exports_create",
        "Create export",
        "Request a new bulk data export job.",
        "{data:{id,accepted}}",
        [("format", "s", False)],
        True,
    ),
    (
        "exports_get",
        "Get export",
        "Fetch the state of one export job.",
        "{data:{id,format,state}}",
        _P_ID,
        False,
    ),
    (
        "exports_download",
        "Download export",
        "Get the download link for a finished export.",
        "{data:{id,url}}",
        _P_ID,
        False,
    ),
    (
        "audit_log_list",
        "List audit log",
        "List workspace audit-log entries such as logins and permission changes (not issue history).",
        "{data:[{id,actor,action}]}",
        _P_PAGE,
        False,
    ),
    (
        "audit_log_get",
        "Get audit entry",
        "Fetch one audit-log entry.",
        "{data:{id,actor,action}}",
        _P_ID,
        False,
    ),
    (
        "audit_log_export",
        "Export audit log",
        "Start an export of the audit log.",
        "{data:{id,accepted}}",
        [],
        True,
    ),
    (
        "sla_policies_list",
        "List SLA policies",
        "List response-time SLA policies.",
        "{data:[{id,name,target_hours}]}",
        _P_PAGE,
        False,
    ),
    (
        "sla_policies_get",
        "Get SLA policy",
        "Fetch one SLA policy.",
        "{data:{id,name,target_hours}}",
        _P_ID,
        False,
    ),
    (
        "sla_policies_create",
        "Create SLA policy",
        "Define a new SLA policy.",
        "{data:{id,accepted}}",
        [("name", "s", True)],
        True,
    ),
    (
        "sla_policies_update",
        "Update SLA policy",
        "Edit an existing SLA policy.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "sla_policies_delete",
        "Delete SLA policy",
        "Delete an SLA policy.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "teams_list",
        "List teams",
        "List the teams in the workspace.",
        "{data:[{id,name,member_count}]}",
        _P_PAGE,
        False,
    ),
    ("teams_get", "Get team", "Fetch one team.", "{data:{id,name}}", _P_ID, False),
    (
        "teams_members_list",
        "List team members",
        "List the members of one team with their role, active flag and on-call state.",
        "{data:[{id,display_name,role,active,on_call}]}",
        [("team_id", "s", True)],
        False,
    ),
    (
        "teams_create",
        "Create team",
        "Create a new team.",
        "{data:{id,accepted}}",
        [("name", "s", True)],
        True,
    ),
    (
        "teams_update",
        "Update team",
        "Rename or edit a team.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "integrations_list",
        "List integrations",
        "List installed third-party integrations.",
        "{data:[{id,provider,state}]}",
        _P_PAGE,
        False,
    ),
    (
        "integrations_get",
        "Get integration",
        "Fetch one installed integration.",
        "{data:{id,provider,state}}",
        _P_ID,
        False,
    ),
    (
        "integrations_connect",
        "Connect integration",
        "Install a third-party integration.",
        "{data:{id,accepted}}",
        [("provider", "s", True)],
        True,
    ),
    (
        "integrations_disconnect",
        "Disconnect integration",
        "Uninstall an integration.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "integrations_sync",
        "Sync integration",
        "Trigger a manual sync of an integration.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "notifications_list",
        "List notifications",
        "List the current user's inbox notifications.",
        "{data:[{id,kind,read}]}",
        _P_PAGE,
        False,
    ),
    (
        "notifications_get",
        "Get notification",
        "Fetch one notification.",
        "{data:{id,kind,read}}",
        _P_ID,
        False,
    ),
    (
        "notifications_mark_read",
        "Mark notification read",
        "Mark one notification as read.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "notifications_mark_all_read",
        "Mark all read",
        "Mark every inbox notification as read.",
        "{data:{accepted}}",
        [],
        True,
    ),
    (
        "notifications_settings_get",
        "Get notification settings",
        "Read notification delivery preferences.",
        "{data:{email,push}}",
        [],
        False,
    ),
    (
        "notifications_settings_update",
        "Update notification settings",
        "Change notification delivery preferences.",
        "{data:{accepted}}",
        [("email", "b", False)],
        True,
    ),
    (
        "issues_history_list",
        "List issue history",
        "List workspace-level change-feed events (batched, not per issue).",
        "{data:[{id,event}]}",
        _P_PAGE,
        False,
    ),
    (
        "issues_watchers_list",
        "List watchers",
        "List watcher subscription records.",
        "{data:[{id,user}]}",
        _P_PAGE,
        False,
    ),
    (
        "issues_attachments_list",
        "List attachments",
        "List attachment metadata records.",
        "{data:[{id,filename}]}",
        _P_PAGE,
        False,
    ),
    (
        "users_get",
        "Get user",
        "Fetch one user by id (display name and email only).",
        "{data:{id,display_name,email}}",
        _P_ID,
        False,
    ),
    (
        "labels_create",
        "Create label",
        "Create a new label definition.",
        "{data:{id,accepted}}",
        [("name", "s", True)],
        True,
    ),
    (
        "labels_delete",
        "Delete label",
        "Delete a label definition.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "cycles_get",
        "Get cycle",
        "Fetch one sprint cycle by id.",
        "{data:{id,name}}",
        _P_ID,
        False,
    ),
    (
        "cycles_create",
        "Create cycle",
        "Create a new sprint cycle.",
        "{data:{id,accepted}}",
        [("name", "s", True)],
        True,
    ),
    (
        "projects_get",
        "Get project",
        "Fetch one project by id.",
        "{data:{id,key,name}}",
        _P_ID,
        False,
    ),
    (
        "reports_list",
        "List reports",
        "List saved analytics reports.",
        "{data:[{id,name,created}]}",
        _P_PAGE,
        False,
    ),
    (
        "reports_run",
        "Run report",
        "Run a saved analytics report.",
        "{data:{id,accepted}}",
        _P_ID,
        True,
    ),
    (
        "issues_subscriptions_list",
        "List subscriptions",
        "List subscription records for the current user.",
        "{data:[{id,issue}]}",
        _P_PAGE,
        False,
    ),
]
assert len(SPECS) == 51


def area_of(name: str) -> str:
    for a in sorted(_AREA, key=len, reverse=True):
        if name.startswith(a + "_"):
            return a
    return name


def _team_name(ref: Any) -> str | None:
    if not isinstance(ref, str):
        return None
    ref = ref.strip()
    for i, n in enumerate(TEAM_NAMES, 1):
        if ref.upper() == f"T{i}" or ref.lower() == n.lower():
            return n
    return None


def stub(store, name: str, args: dict) -> Any:
    """Plausible, deterministic, read-only stub data. Never mutates the store."""
    args = args if isinstance(args, dict) else {}
    if name == "teams_members_list":
        team = _team_name(args.get("team_id"))
        if team is None:
            raise DomainError(
                "not_found",
                f"team {args.get('team_id')!r} not found",
                "Use a team id from teams_list.",
                "team_id",
            )
        return {
            "data": [
                {
                    "id": u["id"],
                    "display_name": u["name"],
                    "role": u["role"],
                    "active": u.get("active", True),
                    "on_call": u.get("on_call", False),
                }
                for u in store.apply("find_users", team=team)
            ]
        }
    if name == "admin_users_list":
        return {
            "data": [
                {
                    "id": u["id"],
                    "display_name": u["name"],
                    "email": f"{u['handle']}@trackly.example",
                    "active": u.get("active", True),
                    "last_login": "2026-10-01",
                }
                for u in store.apply("find_users")
            ],
            "page": 1,
        }
    if name == "users_get":
        u = store.user(str(args.get("id", "")).upper())
        if u is None:
            raise DomainError(
                "not_found",
                f"user {args.get('id')!r} not found",
                "Use a user id such as U12.",
                "id",
            )
        return {
            "data": {
                "id": u["id"],
                "display_name": u["name"],
                "email": f"{u['handle']}@trackly.example",
            }
        }
    if name == "teams_list":
        counts = {n: len(store.apply("find_users", team=n)) for n in TEAM_NAMES}
        return {
            "data": [
                {"id": f"T{i}", "name": n, "member_count": counts[n]}
                for i, n in enumerate(TEAM_NAMES, 1)
            ],
            "page": 1,
        }
    kind, prefix = _AREA.get(area_of(name), ("item", "X"))
    if name.endswith("_list"):
        return {
            "data": [
                {
                    "id": f"{prefix}-{i}",
                    "name": f"{kind} {i}",
                    "active": True,
                    "created": f"2026-09-0{i}",
                }
                for i in (1, 2, 3)
            ],
            "page": 1,
        }
    if (
        name.endswith("_get")
        or name.endswith("settings_get")
        or name == "exports_download"
    ):
        return {
            "data": {
                "id": f"{prefix}-1",
                "name": f"{kind} 1",
                "active": True,
                "url": "https://example.invalid/stub",
            }
        }
    return {
        "data": {
            "id": f"{prefix}-1",
            "accepted": True,
            "note": "stub response; no workspace data changed",
        }
    }
