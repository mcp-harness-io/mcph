"""The ``oauth.*`` rules of ``mcph doctor``.

They look at the OAuth setup an HTTP server announces, which takes network
access, so the checks themselves run in :mod:`mcph.oauth_audit`. Their ids and
descriptions are declared here so that they are configured like every other rule.
"""

from __future__ import annotations

from mcph.rules.base import RuleInfo, Severity

METADATA = "oauth.metadata"
ISSUER = "oauth.issuer"
PKCE = "oauth.pkce"
REGISTRATION = "oauth.registration"

OAUTH_RULES: tuple[RuleInfo, ...] = (
    RuleInfo(
        METADATA,
        "The authorization server's metadata can be found and has an authorization "
        "and a token endpoint.",
        Severity.ERROR,
    ),
    RuleInfo(
        ISSUER,
        "The metadata's issuer is the authorization server the resource announced "
        "(a difference of only a trailing slash is a warning).",
        Severity.ERROR,
    ),
    RuleInfo(PKCE, "PKCE with S256 is advertised.", Severity.ERROR),
    RuleInfo(REGISTRATION, "Dynamic client registration is offered.", Severity.WARN),
)
