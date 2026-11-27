"""Applying the rule settings of ``mcph.yaml`` to rules and their results.

``doctor.rules`` maps a rule id to ``error``, ``warn`` or ``off``. Rules that are
not mentioned keep their default severity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from mcph.rules.base import RuleResult, Severity

OFF = "off"


def is_off(rule_id: str, settings: Mapping[str, str]) -> bool:
    """Whether the settings turn the rule off, so it must not run."""
    return settings.get(rule_id) == OFF


def apply_rule_settings(result: RuleResult, settings: Mapping[str, str]) -> RuleResult:
    """The result as ``mcph.yaml`` wants it shown.

    A rule that is set to ``off`` has no findings and says so; the findings of a
    rule with another setting get the configured severity.
    """
    setting = settings.get(result.rule_id)
    if setting is None:
        return result
    if setting == OFF:
        return RuleResult.off(result.rule_id)
    severity = Severity(setting)
    return replace(
        result,
        findings=tuple(replace(finding, severity=severity) for finding in result.findings),
    )
