"""Doctor rules: checks of what a server exposes and announces.

``base`` is the engine, ``settings`` applies the ``doctor.rules`` section of
``mcph.yaml``, and ``builtin`` holds the rules that ship with mcph.
"""

from mcph.rules.base import (
    Finding,
    Outcome,
    Rule,
    RuleInfo,
    RuleResult,
    Severity,
    run_rule,
    run_rules,
)
from mcph.rules.builtin import BUILTIN_RULES, OAUTH_RULES
from mcph.rules.settings import apply_rule_settings, is_off


def known_rules() -> dict[str, RuleInfo]:
    """Every built-in rule by id: what can be configured in ``doctor.rules``."""
    return {rule.id: rule for rule in (*BUILTIN_RULES, *OAUTH_RULES)}


__all__ = [
    "BUILTIN_RULES",
    "OAUTH_RULES",
    "Finding",
    "Outcome",
    "Rule",
    "RuleInfo",
    "RuleResult",
    "Severity",
    "apply_rule_settings",
    "is_off",
    "known_rules",
    "run_rule",
    "run_rules",
]
