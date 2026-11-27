"""Find out what authentication an HTTP MCP server asks for.

A server that requires OAuth answers an unauthenticated request with 401 and
points clients at its protected resource metadata (RFC 9728): in the
``resource_metadata`` parameter of the ``WWW-Authenticate`` challenge, or at a
well-known URL. This module only reads that information; it never sends
credentials.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx2

from mcph import __version__

_INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "mcph", "version": __version__},
    },
}

# key="quoted value" or key=token, as used in WWW-Authenticate challenges.
_CHALLENGE_PARAM = re.compile(
    r'([A-Za-z][\w-]*)=(?:"((?:[^"\\]|\\.)*)"|([^\s,"]+))'
)

_WELL_KNOWN = "/.well-known/oauth-protected-resource"


@dataclass(frozen=True)
class AuthInfo:
    """What an HTTP server says about the authentication it requires."""

    challenge: str | None
    """The ``WWW-Authenticate`` header of the 401 response, if any."""
    metadata_url: str | None = None
    """Where the protected resource metadata was found, if it was."""
    authorization_servers: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()


async def probe_auth(
    url: str,
    *,
    timeout: float,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> AuthInfo | None:
    """Return the server's auth requirements if it answers 401, else ``None``.

    Sends one unauthenticated MCP ``initialize`` request to ``url``. Every other
    outcome (another status, a network error, a timeout) gives ``None``: this
    only explains a failure and must never hide it. ``transport`` is a test seam.
    """
    try:
        async with asyncio.timeout(timeout):
            async with httpx2.AsyncClient(
                follow_redirects=True, timeout=timeout, transport=transport
            ) as http:
                return await _probe(http, url)
    except (httpx2.HTTPError, TimeoutError, ValueError):
        return None


async def _probe(http: httpx2.AsyncClient, url: str) -> AuthInfo | None:
    response = await http.post(
        url,
        json=_INITIALIZE_REQUEST,
        headers={"Accept": "application/json, text/event-stream"},
    )
    if response.status_code != 401:
        return None

    challenge = response.headers.get("www-authenticate")
    for candidate in _metadata_candidates(url, challenge):
        metadata = await _fetch_json_object(http, candidate)
        if metadata is not None:
            return AuthInfo(
                challenge=challenge,
                metadata_url=candidate,
                authorization_servers=_strings(metadata.get("authorization_servers")),
                scopes=_strings(metadata.get("scopes_supported")),
            )
    return AuthInfo(challenge=challenge)


def _metadata_candidates(url: str, challenge: str | None) -> list[str]:
    """Where to look for the metadata: the challenge's pointer, then well-known URLs."""
    candidates: list[str] = []
    advertised = _challenge_params(challenge).get("resource_metadata")
    if advertised and urlsplit(advertised).scheme in ("http", "https"):
        candidates.append(advertised)

    parts = urlsplit(url)
    root = f"{parts.scheme}://{parts.netloc}{_WELL_KNOWN}"
    if parts.path.rstrip("/"):
        candidates.append(root + parts.path.rstrip("/"))
    candidates.append(root)
    return list(dict.fromkeys(candidates))


def _challenge_params(challenge: str | None) -> dict[str, str]:
    """The parameters of a ``WWW-Authenticate`` header; the first of each name wins."""
    params: dict[str, str] = {}
    for match in _CHALLENGE_PARAM.finditer(challenge or ""):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        params.setdefault(match.group(1).lower(), value)
    return params


async def _fetch_json_object(http: httpx2.AsyncClient, url: str) -> dict | None:
    try:
        response = await http.get(url, headers={"Accept": "application/json"})
        data = response.json() if response.status_code == 200 else None
    except (httpx2.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
