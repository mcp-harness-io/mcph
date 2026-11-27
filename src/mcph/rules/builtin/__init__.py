"""The rules that ship with mcph, one module each."""

from mcph.rules.base import Rule
from mcph.rules.builtin.oauth import OAUTH_RULES
from mcph.rules.builtin.tool_name_snake_case import TOOL_NAME_SNAKE_CASE

# The rules that look at what a server exposes; `mcph doctor` runs these.
BUILTIN_RULES: tuple[Rule, ...] = (TOOL_NAME_SNAKE_CASE,)

__all__ = ["BUILTIN_RULES", "OAUTH_RULES"]
