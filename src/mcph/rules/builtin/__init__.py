"""The rules that ship with mcph, one module each."""

from mcph.rules.base import Rule, RuleInfo
from mcph.rules.builtin.oauth import OAUTH_RULES
from mcph.rules.builtin.tool_name_snake_case import TOOL_NAME_SNAKE_CASE

BUILTIN_RULES: tuple[Rule, ...] = (TOOL_NAME_SNAKE_CASE, *OAUTH_RULES)


def known_rules() -> dict[str, RuleInfo]:
    """Every built-in rule by id: what can be configured in ``doctor.rules``."""
    return {rule.id: rule for rule in BUILTIN_RULES}


__all__ = ["BUILTIN_RULES", "OAUTH_RULES", "known_rules"]
