"""Doctor rules: checks that inspect what a server exposes.

A rule looks at a :class:`~mcph.mcp_client.ServerSnapshot` and reports a message
for each problem it finds. The engine turns those messages into
:class:`Finding` objects carrying the rule's id and severity.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from mcph.mcp_client import ServerSnapshot


class Severity(StrEnum):
    """How serious a finding is."""

    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    """One problem a rule found on a server."""

    rule_id: str
    severity: Severity
    message: str


@dataclass(frozen=True)
class Rule:
    """A named check with a default severity.

    ``check`` returns one message per problem it finds (none if all is well).
    """

    id: str
    description: str
    severity: Severity
    check: Callable[[ServerSnapshot], Iterable[str]]


# Lowercase letters and digits, words separated by single underscores.
_SNAKE_CASE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def to_snake_case(name: str) -> str:
    """Best-effort conversion to snake_case (``getOrder`` -> ``get_order``)."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)  # HTTPServer -> HTTP_Server
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)  # getOrder -> get_Order
    name = re.sub(r"\W+", "_", name)  # get-order, list orders -> get_order, ...
    return re.sub(r"_+", "_", name).strip("_").lower()


def _tool_names_are_snake_case(snapshot: ServerSnapshot) -> Iterator[str]:
    for tool in snapshot.tools:
        if _SNAKE_CASE.fullmatch(tool.name):
            continue
        message = f"tool {tool.name!r} is not snake_case"
        suggestion = to_snake_case(tool.name)
        if _SNAKE_CASE.fullmatch(suggestion):
            message += f" (try {suggestion!r})"
        yield message


TOOL_NAME_SNAKE_CASE = Rule(
    id="core.tool_name_snake_case",
    description="Tool names should be snake_case.",
    severity=Severity.WARN,
    check=_tool_names_are_snake_case,
)

BUILTIN_RULES: tuple[Rule, ...] = (TOOL_NAME_SNAKE_CASE,)


def run_rules(
    snapshot: ServerSnapshot, rules: Iterable[Rule] = BUILTIN_RULES
) -> list[Finding]:
    """Run ``rules`` against a server snapshot and collect their findings."""
    return [
        Finding(rule.id, rule.severity, message)
        for rule in rules
        for message in rule.check(snapshot)
    ]
