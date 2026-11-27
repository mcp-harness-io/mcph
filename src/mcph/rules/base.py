"""The rule engine: what a rule is and how it is run against a server.

A rule looks at one server through a :class:`RuleContext` and reports what is
wrong with it. The engine turns what a check returns into :class:`Finding`
objects carrying the rule's id and severity, and a rule's :class:`RuleResult`
says what happened to it on that server.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mcph.config import ServerConfig
    from mcph.mcp_client import ServerError, ServerSnapshot

NO_CONNECTION = "mcph could not connect to the server"
"""Why a rule that needs the server was skipped when the server failed."""


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
class Issue:
    """A problem that a rule reports with a severity other than its default."""

    message: str
    severity: Severity | None = None


class SkipRule(Exception):
    """Raised by a check that has nothing to look at on this server; the message says why."""


@dataclass(frozen=True)
class RuleContext:
    """What a rule can look at on one server."""

    server: ServerConfig
    """The entry of ``mcph.yaml`` that is being checked."""
    timeout: float
    """Seconds a rule may spend on each network request."""
    snapshot: ServerSnapshot | None = None
    """What the server exposes; ``None`` if mcph could not connect to it."""
    error: ServerError | None = None
    """Why mcph could not connect; ``None`` if it did."""
    cache: dict[str, Any] = field(default_factory=dict)
    """Shared by all the rules checking this server, for lookups several need."""


@dataclass(frozen=True)
class RuleInfo:
    """What a rule is called, what it looks for, and how serious a finding usually is."""

    id: str
    description: str
    severity: Severity


Check = Callable[[RuleContext], "Iterable[str | Issue] | Awaitable[Iterable[str | Issue]]"]


@dataclass(frozen=True)
class Rule(RuleInfo):
    """A named check with a default severity.

    ``check`` may be a plain or an ``async`` function. It returns one message per
    problem it finds, or :class:`Issue` objects that carry their own severity, and
    returns nothing if all is well. It may raise :class:`SkipRule`.
    """

    check: Check
    transports: tuple[str, ...] = ("stdio", "http")
    """The kinds of server the rule is about."""
    needs_connection: bool = True
    """Whether the rule looks at ``context.snapshot``, so it cannot run on a server
    that failed to start or connect."""

    def applies_to(self, server: ServerConfig) -> bool:
        """Whether the rule is about this kind of server (and so is listed for it)."""
        return server.transport in self.transports


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


async def run_rule(rule: Rule, context: RuleContext) -> RuleResult:
    """Run one rule on one server.

    A rule that needs the server is skipped if mcph could not connect to it. A
    rule that raises :class:`SkipRule` is skipped with its reason. Any other
    exception is a bug in the rule: it is reported as an error finding instead of
    ending the run.
    """
    if rule.needs_connection and context.snapshot is None:
        return RuleResult.skipped(rule.id, NO_CONNECTION)
    try:
        found = rule.check(context)
        if inspect.isawaitable(found):
            found = await found
        findings = tuple(_finding(rule, item) for item in found)
    except SkipRule as skip:
        return RuleResult.skipped(rule.id, str(skip))
    except Exception as exc:  # a broken rule must not end the run
        failure = f"the rule failed: {type(exc).__name__}: {exc}"
        return RuleResult(rule.id, findings=(Finding(rule.id, Severity.ERROR, failure),))
    return RuleResult(rule.id, findings=findings)


def _finding(rule: Rule, item: str | Issue) -> Finding:
    if isinstance(item, Issue):
        return Finding(rule.id, item.severity or rule.severity, item.message)
    if isinstance(item, str):
        return Finding(rule.id, rule.severity, item)
    raise TypeError(f"a check returns messages or Issue objects, not {type(item).__name__}")
