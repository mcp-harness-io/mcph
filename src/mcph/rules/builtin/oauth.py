"""The ``oauth.*`` rules of ``mcph doctor run``.

They look at the OAuth setup an HTTP server announces, which takes network
access. The four rules share one lookup per server, kept in the cache of the rule
context: the authorization server the server announced and what its metadata
says. The lookup and the checks are in :mod:`mcph.oauth_audit`.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcph.config import HttpServerConfig, OAuthAuthConfig
from mcph.oauth_audit import (
    ISSUER,
    METADATA,
    PKCE,
    REGISTRATION,
    check_metadata,
    discover_metadata,
)
from mcph.rules.base import (
    NO_CONNECTION,
    Finding,
    Issue,
    Rule,
    RuleContext,
    Severity,
    SkipRule,
)

NOT_NEEDED = "not needed, the server accepted the connection"
NO_AUTHORIZATION_SERVER = "the server announced no authorization server"
NO_METADATA = "the authorization server's metadata was not found"

_LOOKUP = "oauth.lookup"


@dataclass(frozen=True)
class _Lookup:
    """What the rules found out about a server, or why there was nothing to check."""

    skip: str = ""
    findings: tuple[Finding, ...] = ()
    metadata_found: bool = False


async def _lookup(context: RuleContext) -> _Lookup:
    if _LOOKUP not in context.cache:
        context.cache[_LOOKUP] = await _look(context)
    return context.cache[_LOOKUP]


async def _look(context: RuleContext) -> _Lookup:
    if context.error is None:
        return _Lookup(skip=NOT_NEEDED)
    auth = context.error.auth
    if auth is None:
        return _Lookup(skip=NO_CONNECTION)
    if not auth.authorization_servers:
        return _Lookup(skip=NO_AUTHORIZATION_SERVER)

    server = context.server
    assert isinstance(server, HttpServerConfig)  # the rules are about http servers only
    accepted = (
        isinstance(server.auth, OAuthAuthConfig)
        and server.auth.ignore_issuer_trailing_slash
    )
    announced = auth.authorization_servers[0]  # like a client, look at the first one
    discovery = await discover_metadata(announced, server.url, timeout=context.timeout)
    findings = check_metadata(announced, discovery, issuer_slash_accepted=accepted)
    return _Lookup(findings=tuple(findings), metadata_found=discovery.metadata is not None)


def _oauth_rule(rule_id: str, description: str, severity: Severity) -> Rule:
    async def check(context: RuleContext) -> list[Issue]:
        lookup = await _lookup(context)
        if lookup.skip:
            raise SkipRule(lookup.skip)
        if not lookup.metadata_found and rule_id != METADATA:
            raise SkipRule(NO_METADATA)  # only finding the metadata could be checked
        return [
            Issue(finding.message, finding.severity)
            for finding in lookup.findings
            if finding.rule_id == rule_id
        ]

    return Rule(
        rule_id,
        description,
        severity,
        check,
        transports=("http",),
        needs_connection=False,
    )


OAUTH_RULES: tuple[Rule, ...] = (
    _oauth_rule(
        METADATA,
        "The authorization server's metadata can be found and has an authorization "
        "and a token endpoint.",
        Severity.ERROR,
    ),
    _oauth_rule(
        ISSUER,
        "The metadata's issuer is the authorization server the resource announced "
        "(a difference of only a trailing slash is a warning).",
        Severity.ERROR,
    ),
    _oauth_rule(PKCE, "PKCE with S256 is advertised.", Severity.ERROR),
    _oauth_rule(REGISTRATION, "Dynamic client registration is offered.", Severity.WARN),
)
