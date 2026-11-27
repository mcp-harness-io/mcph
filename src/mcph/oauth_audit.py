"""Check the OAuth setup that an HTTP server announces.

A server that needs authentication announces an authorization server in its
protected resource metadata (RFC 9728). Whether a client can really sign in
depends on that server's own metadata (RFC 8414, or OpenID Connect discovery).
These checks read it with plain unauthenticated requests, never sending any
credential, and report what would stop a client.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx2
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    issuers_match,
)

from mcph.auth_probe import AuthInfo
from mcph.rules import Finding, Severity

ISSUER = "oauth.issuer"
METADATA = "oauth.metadata"
PKCE = "oauth.pkce"
REGISTRATION = "oauth.registration"


@dataclass(frozen=True)
class Discovery:
    """The outcome of looking for an authorization server's metadata."""

    url: str | None
    """Where the metadata was found, if it was."""
    metadata: dict | None
    tried: tuple[str, ...]
    """Every URL that was asked, with what came back (``url (404)``)."""


async def discover_metadata(
    announced: str,
    resource_url: str,
    *,
    timeout: float,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> Discovery:
    """Look for the metadata of the authorization server ``announced``.

    Asks the URLs a client would, in its order (RFC 8414, then OpenID Connect
    discovery), and takes the first that answers with a JSON object. ``transport``
    is a test seam. Never raises: whatever goes wrong is part of ``tried``.
    """
    tried: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            async with httpx2.AsyncClient(
                follow_redirects=True, timeout=timeout, transport=transport
            ) as http:
                for url in build_oauth_authorization_server_metadata_discovery_urls(
                    announced, resource_url
                ):
                    document, outcome = await _get_json_object(http, url)
                    tried.append(f"{url} ({outcome})")
                    if document is not None:
                        return Discovery(url, document, tuple(tried))
    except TimeoutError:
        tried.append(f"gave up after {timeout:g} s")
    return Discovery(None, None, tuple(tried))


async def _get_json_object(
    http: httpx2.AsyncClient, url: str
) -> tuple[dict | None, str]:
    try:
        response = await http.get(url, headers={"Accept": "application/json"})
    except httpx2.HTTPError as exc:
        return None, type(exc).__name__
    if response.status_code != 200:
        return None, str(response.status_code)
    try:
        data = response.json()
    except ValueError:
        return None, "200, not JSON"
    if not isinstance(data, dict):
        return None, "200, not a JSON object"
    return data, "200"


def check_metadata(
    announced: str, discovery: Discovery, *, issuer_slash_accepted: bool = False
) -> list[Finding]:
    """What is wrong with an authorization server for a client that wants to sign in.

    ``announced`` is the issuer the resource server announced for it.
    ``issuer_slash_accepted`` says that the config already tolerates a difference
    of only a trailing slash.
    """
    metadata = discovery.metadata
    if metadata is None:
        tried = "; ".join(discovery.tried) or "nothing"
        return [
            Finding(
                METADATA,
                Severity.ERROR,
                f"cannot find the metadata of the authorization server {announced} "
                f"(tried: {tried})",
            )
        ]

    findings: list[Finding] = []
    missing = [
        key
        for key in ("authorization_endpoint", "token_endpoint")
        if not isinstance(metadata.get(key), str) or not metadata[key]
    ]
    if missing:
        findings.append(
            Finding(
                METADATA,
                Severity.ERROR,
                f"the metadata at {discovery.url} has no {' or '.join(missing)}",
            )
        )

    issuer = metadata.get("issuer")
    if not isinstance(issuer, str) or not issuer:
        findings.append(
            Finding(
                ISSUER, Severity.ERROR, f"the metadata at {discovery.url} has no issuer"
            )
        )
    elif issuer != announced:
        findings.append(_issuer_finding(announced, issuer, issuer_slash_accepted))

    methods = metadata.get("code_challenge_methods_supported")
    if not (isinstance(methods, list) and "S256" in methods):
        shown = ", ".join(map(str, methods)) if isinstance(methods, list) else "absent"
        findings.append(
            Finding(
                PKCE,
                Severity.ERROR,
                "the authorization server does not advertise PKCE with S256 "
                f"(code_challenge_methods_supported: {shown or 'empty'}); "
                "MCP clients require it",
            )
        )

    if not metadata.get("registration_endpoint"):
        findings.append(
            Finding(
                REGISTRATION,
                Severity.WARN,
                "the authorization server offers no registration_endpoint, so clients "
                "cannot register themselves; mcph cannot sign in without dynamic "
                "client registration yet",
            )
        )
    return findings


def _issuer_finding(announced: str, issuer: str, slash_accepted: bool) -> Finding:
    said = (
        f"the resource announces its authorization server as {announced} "
        f"but that server's metadata says {issuer}"
    )
    if not issuers_match(announced, issuer):
        return Finding(
            ISSUER,
            Severity.ERROR,
            f"{said}; RFC 8414 requires them to be identical, so clients refuse "
            "to sign in",
        )
    if slash_accepted:
        return Finding(
            ISSUER,
            Severity.WARN,
            f"{said}; they differ only by a trailing slash, which "
            "`ignore_issuer_trailing_slash` makes mcph accept",
        )
    return Finding(
        ISSUER,
        Severity.WARN,
        f"{said}; they differ only by a trailing slash, which strict clients "
        "refuse (RFC 8414); set `ignore_issuer_trailing_slash: true` under `auth` "
        "to have mcph accept it",
    )


async def audit_authorization_server(
    auth: AuthInfo,
    resource_url: str,
    *,
    timeout: float,
    issuer_slash_accepted: bool = False,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> tuple[Finding, ...]:
    """Check the authorization server a server announced in what it told us.

    Does nothing if the server announced none (there is nothing to check). Like
    a client, it looks at the first one announced.
    """
    if not auth.authorization_servers:
        return ()
    announced = auth.authorization_servers[0]
    discovery = await discover_metadata(
        announced, resource_url, timeout=timeout, transport=transport
    )
    return tuple(
        check_metadata(
            announced, discovery, issuer_slash_accepted=issuer_slash_accepted
        )
    )
