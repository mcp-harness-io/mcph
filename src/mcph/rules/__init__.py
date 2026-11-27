"""Doctor rules: checks of what a server exposes and announces.

``base`` is the engine and ``settings`` applies the ``doctor.rules`` section of
``mcph.yaml``. The rules that ship with mcph are in ``mcph.rules.builtin``, which
is not imported here so that the engine stays free of them.
"""

from mcph.rules.base import (
    NO_CONNECTION,
    Finding,
    Issue,
    Outcome,
    Rule,
    RuleContext,
    RuleInfo,
    RuleResult,
    Severity,
    SkipRule,
    run_rule,
)
from mcph.rules.settings import apply_rule_settings, is_off

__all__ = [
    "NO_CONNECTION",
    "Finding",
    "Issue",
    "Outcome",
    "Rule",
    "RuleContext",
    "RuleInfo",
    "RuleResult",
    "Severity",
    "SkipRule",
    "apply_rule_settings",
    "is_off",
    "run_rule",
]
