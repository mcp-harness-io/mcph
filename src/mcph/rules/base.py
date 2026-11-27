"""The rule engine: findings, rules, and running rules against a server.

A rule looks at a :class:`~mcph.mcp_client.ServerSnapshot` and reports a message
for each problem it finds. The engine turns those messages into
:class:`Finding` objects carrying the rule's id and severity, and a rule's
:class:`RuleResult` says what happened to it on one server.
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


class Outcome(StrEnum):
    """What happened to a rule on one server."""

    RAN = "ran"
    OFF = "off"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RuleResult:
    """What happened to one rule on one server.

    A rule that ran has the findings it reported; none means it passed. A rule
    that was turned off, or that could not run, has no findings, and a skipped
    rule says why.
    """

    rule_id: str
    outcome: Outcome = Outcome.RAN
    findings: tuple[Finding, ...] = ()
    reason: str = ""

    @classmethod
    def off(cls, rule_id: str) -> RuleResult:
        """The rule is turned off in ``mcph.yaml``, so it did not run."""
        return cls(rule_id, Outcome.OFF)

    @classmethod
    def skipped(cls, rule_id: str, reason: str) -> RuleResult:
        """The rule did not run because it could not, or was not needed."""
        return cls(rule_id, Outcome.SKIPPED, reason=reason)


def run_rule(rule: Rule, snapshot: ServerSnapshot) -> RuleResult:
    """Run one rule against a server snapshot."""
    findings = tuple(
        Finding(rule.id, rule.severity, message) for message in rule.check(snapshot)
    )
    return RuleResult(rule.id, findings=findings)


def run_rules(snapshot: ServerSnapshot, rules: Iterable[Rule]) -> list[Finding]:
    """Run ``rules`` against a server snapshot and collect their findings."""
    return [
        finding for rule in rules for finding in run_rule(rule, snapshot).findings
    ]
