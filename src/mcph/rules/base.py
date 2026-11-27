"""The rule engine: findings, rules, and running rules against a server.

A rule looks at a :class:`~mcph.mcp_client.ServerSnapshot` and reports a message
for each problem it finds. The engine turns those messages into
:class:`Finding` objects carrying the rule's id and severity.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
class RuleInfo:
    """What a rule is called, what it looks for, and how serious a finding usually is."""

    id: str
    description: str
    severity: Severity


@dataclass(frozen=True)
class Rule(RuleInfo):
    """A named check with a default severity.

    ``check`` returns one message per problem it finds (none if all is well).
    """

    check: Callable[[ServerSnapshot], Iterable[str]]


def run_rules(snapshot: ServerSnapshot, rules: Iterable[Rule]) -> list[Finding]:
    """Run ``rules`` against a server snapshot and collect their findings."""
    return [
        Finding(rule.id, rule.severity, message)
        for rule in rules
        for message in rule.check(snapshot)
    ]
