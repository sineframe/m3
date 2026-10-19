"""The full rule catalogue, harness and directory rules, and its evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .collect import Snapshot
from .directories import Directory, DirectoryEffect
from .directory_rules import (
    DIRECTORY_RULES,
    SHARED_DIRECTORY_EFFECTS,
    SHARED_DIRECTORY_FIXES,
)
from .rules import (
    HARNESS_RULES,
    SEVERITY,
    SEVERITY_RANK,
    Effect,
    Finding,
    Hit,
    Rule,
)
from .versions import Target

RULES: tuple[Rule, ...] = (
    *(
        replace(
            rule,
            directory_effects=SHARED_DIRECTORY_EFFECTS[rule.id],
            directory_fix=SHARED_DIRECTORY_FIXES.get(rule.id),
        )
        if rule.id in SHARED_DIRECTORY_EFFECTS
        else rule
        for rule in HARNESS_RULES
    ),
    *DIRECTORY_RULES,
)
RULE_ORDER = {rule.id: index for index, rule in enumerate(RULES)}


def _applies(effect: Effect, hit: Hit, targets: list[Target]) -> bool:
    if effect.when is not None and not effect.when & hit.variants:
        return False
    return any(
        target.harness == effect.harness and effect.range.contains(target.version)
        for target in targets
    )


def _applies_directory(
    effect: DirectoryEffect, hit: Hit, directories: Sequence[Directory]
) -> bool:
    if effect.when is not None and not effect.when & hit.variants:
        return False
    return effect.directory in directories


def evaluate(
    snapshot: Snapshot,
    targets: list[Target],
    ignore: set[str],
    directories: Sequence[Directory] = (),
) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        if rule.id in ignore:
            continue
        # A rule with nothing to report for this run is not even checked.
        if not (targets and rule.effects) and not (
            directories and rule.directory_effects
        ):
            continue
        for hit in rule.check(snapshot):
            effects = tuple(e for e in rule.effects if _applies(e, hit, targets))
            directory_effects = tuple(
                e
                for e in rule.directory_effects
                if _applies_directory(e, hit, directories)
            )
            if not effects and not directory_effects:
                continue
            severity = min(
                (
                    *(SEVERITY[e.impact] for e in effects),
                    *(e.severity for e in directory_effects),
                ),
                key=SEVERITY_RANK.__getitem__,
            )
            findings.append(Finding(rule, hit, effects, severity, directory_effects))
    return findings
