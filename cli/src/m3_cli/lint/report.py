"""The `m3 lint` report: JSON shape, human text and the exit code."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from ..terminal import stream_is_utf8
from .collect import Snapshot
from .rules import RULE_ORDER, RULES, SEVERITY_RANK, Effect, Finding
from .versions import KNOWN_LATEST, Target, VersionRange

if TYPE_CHECKING:
    from m3._terminal import Style

SCHEMA_VERSION = 1


def http_target(url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return {
        "kind": "http",
        "url": urlunsplit((parts.scheme, netloc, parts.path, "", "")),
    }


def _text(value: Any) -> str | None:
    return value[:120] if isinstance(value, str) else None


def _effect(effect: Effect) -> dict[str, Any]:
    return {
        "harness": effect.harness,
        "range": {"introduced": effect.range.introduced, "fixed": effect.range.fixed},
        "impact": effect.impact,
        "evidence": effect.evidence,
        "summary": effect.summary,
    }


def _count(listing: Any) -> int | None:
    return None if listing is None else len(listing.items)


def exit_code(counts: dict[str, int], fail_on: str) -> int:
    if fail_on == "error":
        return 1 if counts["error"] else 0
    if fail_on == "warning":
        return 1 if counts["error"] or counts["warning"] else 0
    return 0


def build_report(
    target: dict[str, Any],
    snapshot: Snapshot,
    targets: list[Target],
    findings: list[Finding],
    fail_on: str,
) -> dict[str, Any]:
    ordered = sorted(
        findings,
        key=lambda f: (
            SEVERITY_RANK[f.severity],
            RULE_ORDER[f.rule.id],
            f.hit.location,
        ),
    )
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in ordered:
        counts[finding.severity] += 1
    info = snapshot.initialize.get("serverInfo")
    info = info if isinstance(info, dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "server": {
            "name": _text(info.get("name")),
            "version": _text(info.get("version")),
            "protocol_version": _text(snapshot.protocol_version),
        },
        "evaluated": [
            {"harness": t.harness, "version": t.version, "beyond_known": t.beyond_known}
            for t in targets
        ],
        "collection": {
            "discover": snapshot.discover,
            "tool_count": len(snapshot.tools.items),
            "tool_pages": snapshot.tools.pages,
            "prompt_count": _count(snapshot.prompts),
            "resource_count": _count(snapshot.resources),
            "template_count": _count(snapshot.templates),
        },
        "counts": counts,
        "findings": [
            {
                "rule": f.rule.id,
                "title": f.rule.title,
                "category": f.rule.category,
                "severity": f.severity,
                "location": f.hit.location,
                "detail": f.hit.detail,
                "effects": [_effect(effect) for effect in f.effects],
                "fix": f.rule.fix,
            }
            for f in ordered
        ],
        "exit_code": exit_code(counts, fail_on),
    }


class _Out:
    """Prints lines, falling back to ASCII when stdout is not UTF-8."""

    def __init__(self) -> None:
        self.unicode = stream_is_utf8(sys.stdout)
        self.separator = " · " if self.unicode else " | "

    def __call__(self, text: str = "") -> None:
        if not self.unicode:
            text = text.encode("ascii", "backslashreplace").decode("ascii")
        print(text)

    def range(self, value: VersionRange) -> str:
        return value.label(unicode=self.unicode)


def _paint(style: Style | None, severity: str, text: str) -> str:
    if style is None:
        return text
    if severity == "error":
        return style.red(text)
    if severity == "warning":
        return style.yellow(text)
    return style.grey(text)


def _optional_count(value: Any, noun: str) -> str:
    return f"{'-' if value is None else value} {noun}"


def _safe(text: str) -> str:
    """Server-derived text with control, format and bidi characters escaped,
    so it cannot rewrite or spoof the terminal."""

    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)


def print_human(report: dict[str, Any], style: Style | None) -> None:
    out = _Out()
    server = report["server"]
    collection = report["collection"]
    name = _safe(server["name"] or "(unnamed)")
    version = _safe(server["version"] or "")
    header = out.separator.join(
        [
            f"m3 lint: {name} {version}".rstrip(),
            f"protocol {_safe(server['protocol_version'] or '-')}",
            f"{collection['tool_count']} tools",
            _optional_count(collection["prompt_count"], "prompts"),
            _optional_count(collection["resource_count"], "resources"),
        ]
    )
    out(style.bold(header) if style is not None else header)
    evaluated = ", ".join(f"{t['harness']}@{t['version']}" for t in report["evaluated"])
    line = f"evaluated: {evaluated}"
    if any(t["beyond_known"] for t in report["evaluated"]):
        line += " (newer than rule data; assuming latest known behaviour)"
    out(line)
    out()
    for finding in report["findings"]:
        severity = finding["severity"]
        label = _paint(style, severity, severity.upper().ljust(7))
        out(f"{label} {finding['rule']}  {_safe(finding['location'])}")
        for effect in finding["effects"]:
            bounds = VersionRange(
                effect["range"]["introduced"], effect["range"]["fixed"]
            )
            out(
                f"  {effect['harness']} {out.range(bounds)} "
                f"[{effect['impact']}]: {effect['summary']}"
            )
        if finding["detail"]:
            out(f"  detail: {_safe(finding['detail'])}")
        out(f"  fix: {finding['fix']}")
        out()
    if not report["findings"]:
        out("no findings")
    counts = report["counts"]
    out(
        f"{counts['error']} errors, {counts['warning']} warnings, {counts['info']} notes"
    )


def print_rules(*, as_json: bool) -> None:
    if as_json:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "known_latest": KNOWN_LATEST,
            "rules": [
                {
                    "id": rule.id,
                    "title": rule.title,
                    "category": rule.category,
                    "fix": rule.fix,
                    "effects": [_effect(effect) for effect in rule.effects],
                }
                for rule in RULES
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    out = _Out()
    for rule in RULES:
        out(f"{rule.id}  {rule.title}")
        for effect in rule.effects:
            out(
                f"  {effect.harness} {out.range(effect.range)} "
                f"[{effect.impact}]: {effect.summary}"
            )
        out()
