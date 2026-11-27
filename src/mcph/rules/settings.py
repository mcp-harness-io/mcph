"""Applying the rule settings of ``mcph.yaml`` to rules and findings.

``doctor.rules`` maps a rule id to ``error``, ``warn`` or ``off``. Rules that are
not mentioned keep their default severity.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace

from mcph.rules.base import Finding, RuleInfo, Severity

OFF = "off"


def enabled_rules[R: RuleInfo](rules: Iterable[R], settings: Mapping[str, str]) -> list[R]:
    """The ``rules`` that are not turned off, in their order."""
    return [rule for rule in rules if settings.get(rule.id) != OFF]


def apply_rule_settings(
    findings: Iterable[Finding], settings: Mapping[str, str]
) -> Iterator[Finding]:
    """Drop the findings of rules set to ``off``; give the rest the configured severity."""
    for finding in findings:
        setting = settings.get(finding.rule_id)
        if setting is None:
            yield finding
        elif setting != OFF:
            yield replace(finding, severity=Severity(setting))
