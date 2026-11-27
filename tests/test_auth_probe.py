import asyncio

import httpx2
import pytest

from mcph.auth_probe import (
    AuthInfo,
    _challenge_params,
    _metadata_candidates,
    probe_auth,
)

URL = "http://localhost:8080/mcp"
PRM = "http://localhost:8080/.well-known/oauth-protected-resource/mcp"
ROOT_PRM = "http://localhost:8080/.well-known/oauth-protected-resource"
CHALLENGE = f'Bearer resource_metadata="{PRM}"'
METADATA = {
    "resource": URL,
    "authorization_servers": ["https://auth.example.com/"],
    "scopes_supported": ["api.read", "api.write"],
}


def unauthorized(challenge=CHALLENGE) -> httpx2.Response:
    headers = {"WWW-Authenticate": challenge} if challenge else {}
    return httpx2.Response(401, headers=headers)


class FakeServer:
    """An HTTP server answering the initialize POST and a few metadata URLs."""

    def __init__(self, post: httpx2.Response, metadata: dict[str, object] | None = None):
        self.post = post
        self.metadata = metadata or {}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.method == "POST":
            return self.post
        body = self.metadata.get(str(request.url))
        if body is None:
            return httpx2.Response(404)
        if isinstance(body, httpx2.Response):
            return body
        return httpx2.Response(200, json=body)

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def probe(handler, *, url=URL, timeout=5.0):
    transport = httpx2.MockTransport(handler)
    return asyncio.run(probe_auth(url, timeout=timeout, transport=transport))


# --- what the probe returns --------------------------------------------------


@pytest.mark.parametrize("status", [200, 202, 403, 404, 405, 500])
def test_servers_that_do_not_answer_401_give_nothing(status):
    server = FakeServer(httpx2.Response(status))

    assert probe(server) is None
    assert server.urls == [URL]  # no metadata is looked up


def test_the_challenge_points_at_the_metadata():
    server = FakeServer(unauthorized(), {PRM: METADATA})

    assert probe(server) == AuthInfo(
        challenge=CHALLENGE,
        metadata_url=PRM,
        authorization_servers=("https://auth.example.com/",),
        scopes=("api.read", "api.write"),
    )


def test_metadata_is_found_at_the_well_known_url_when_the_challenge_has_no_pointer():
    server = FakeServer(unauthorized('Bearer realm="mcp"'), {PRM: METADATA})

    info = probe(server)

    assert info.challenge == 'Bearer realm="mcp"'
    assert info.metadata_url == PRM
    assert info.authorization_servers == ("https://auth.example.com/",)


def test_the_root_well_known_url_is_the_last_resort():
    server = FakeServer(unauthorized('Bearer realm="mcp"'), {ROOT_PRM: METADATA})

    assert probe(server).metadata_url == ROOT_PRM
    assert server.urls == [URL, PRM, ROOT_PRM]


def test_the_advertised_pointer_is_tried_first_then_the_well_known_urls():
    pointer = "http://localhost:8080/custom/prm"
    server = FakeServer(
        unauthorized(f'Bearer resource_metadata="{pointer}"'), {PRM: METADATA}
    )

    info = probe(server)

    assert server.urls == [URL, pointer, PRM]
    assert info.metadata_url == PRM


def test_a_pointer_that_is_not_http_is_never_requested():
    server = FakeServer(
        unauthorized('Bearer resource_metadata="file:///etc/passwd"'), {PRM: METADATA}
    )

    probe(server)

    assert all("etc/passwd" not in url for url in server.urls)


def test_without_any_metadata_only_the_challenge_is_reported():
    server = FakeServer(unauthorized('Bearer realm="mcp"'))

    assert probe(server) == AuthInfo(challenge='Bearer realm="mcp"')


def test_a_401_without_a_challenge_header_is_still_reported():
    server = FakeServer(unauthorized(challenge=None))

    assert probe(server) == AuthInfo(challenge=None)


def test_metadata_that_is_not_a_json_object_is_skipped():
    garbage = httpx2.Response(200, text="<html>not json</html>")
    a_list = httpx2.Response(200, json=["not", "an", "object"])
    server = FakeServer(unauthorized(), {PRM: garbage, ROOT_PRM: a_list})

    assert probe(server) == AuthInfo(challenge=CHALLENGE)


def test_entries_of_the_wrong_type_are_dropped():
    odd = {"authorization_servers": ["https://a/", 5, None], "scopes_supported": "api"}
    server = FakeServer(unauthorized(), {PRM: odd})

    info = probe(server)

    assert info.authorization_servers == ("https://a/",)
    assert info.scopes == ()


# --- failures never hide the original error -----------------------------------


def test_network_errors_give_nothing():
    def refuse(request):
        raise httpx2.ConnectError("refused")

    assert probe(refuse) is None


def test_a_server_that_is_too_slow_gives_nothing():
    async def slow(request):
        await asyncio.sleep(5)
        return httpx2.Response(401)

    assert probe(slow, timeout=0.1) is None


def test_errors_while_fetching_metadata_still_report_the_401():
    def handler(request):
        if request.method == "POST":
            return unauthorized()
        raise httpx2.ConnectError("refused")

    assert probe(handler) == AuthInfo(challenge=CHALLENGE)


# --- what is sent ---------------------------------------------------------------


def test_only_an_unauthenticated_initialize_request_is_sent():
    server = FakeServer(unauthorized(), {PRM: METADATA})

    probe(server)

    post, *gets = server.requests
    assert post.method == "POST" and str(post.url) == URL
    assert b'"method":"initialize"' in post.content.replace(b" ", b"")
    assert "application/json" in post.headers["accept"]
    assert all(request.method == "GET" for request in gets)
    assert all("authorization" not in request.headers for request in server.requests)


# --- parsing ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("challenge", "expected"),
    [
        ('Bearer resource_metadata="https://x/prm"', "https://x/prm"),
        (
            'Bearer realm="mcp", error="invalid_token", resource_metadata="https://x/prm"',
            "https://x/prm",
        ),
        ('Bearer RESOURCE_METADATA="https://x/prm"', "https://x/prm"),
        ("Bearer resource_metadata=https://x/prm", "https://x/prm"),
        (
            'Bearer resource_metadata="https://x/a", resource_metadata="https://x/b"',
            "https://x/a",
        ),
        ('Bearer realm="mcp"', None),
        ("Bearer", None),
        ("", None),
        (None, None),
    ],
)
def test_resource_metadata_is_read_from_the_challenge(challenge, expected):
    assert _challenge_params(challenge).get("resource_metadata") == expected


WELL_KNOWN = "http://h:8080/.well-known/oauth-protected-resource"


@pytest.mark.parametrize(
    ("url", "challenge", "expected"),
    [
        ("http://h:8080/mcp", None, [WELL_KNOWN + "/mcp", WELL_KNOWN]),
        ("http://h:8080/mcp/", None, [WELL_KNOWN + "/mcp", WELL_KNOWN]),
        ("http://h:8080/", None, [WELL_KNOWN]),
        ("http://h:8080", None, [WELL_KNOWN]),
        (
            "http://h:8080/api/mcp",
            'Bearer resource_metadata="http://h:8080/prm"',
            ["http://h:8080/prm", WELL_KNOWN + "/api/mcp", WELL_KNOWN],
        ),
        (  # a pointer equal to a well-known URL is not tried twice
            "http://h:8080/mcp",
            f'Bearer resource_metadata="{WELL_KNOWN}/mcp"',
            [WELL_KNOWN + "/mcp", WELL_KNOWN],
        ),
        (  # only http(s) pointers are followed
            "http://h:8080/mcp",
            'Bearer resource_metadata="ftp://h/prm"',
            [WELL_KNOWN + "/mcp", WELL_KNOWN],
        ),
    ],
)
def test_where_the_metadata_is_looked_for(url, challenge, expected):
    assert _metadata_candidates(url, challenge) == expected


# --- sending a bearer token -----------------------------------------------------------

TOKEN = "s3cr3t-token"


def probe_with_token(handler, *, token=TOKEN, url=URL):
    transport = httpx2.MockTransport(handler)
    return asyncio.run(probe_auth(url, timeout=5.0, token=token, transport=transport))


def test_the_token_is_sent_to_the_mcp_endpoint_and_nowhere_else():
    server = FakeServer(unauthorized(), {PRM: METADATA})

    probe_with_token(server)

    post, *gets = server.requests
    assert post.headers["authorization"] == f"Bearer {TOKEN}"
    assert gets, "the metadata should have been fetched"
    assert all("authorization" not in request.headers for request in gets)


def test_a_refused_token_is_explained_by_the_challenge():
    challenge = (
        'Bearer error="invalid_token", error_description="The token expired", '
        f'scope="api.write", resource_metadata="{PRM}"'
    )
    server = FakeServer(unauthorized(challenge), {PRM: METADATA})

    info = probe_with_token(server)

    assert info.status == 401
    assert info.error == "invalid_token"
    assert info.error_description == "The token expired"
    assert info.required_scope == "api.write"
    assert info.metadata_url == PRM
    assert info.authorization_servers == ("https://auth.example.com/",)


def test_a_403_for_a_missing_scope_is_explained():
    challenge = 'Bearer error="insufficient_scope", scope="api.write"'
    server = FakeServer(httpx2.Response(403, headers={"WWW-Authenticate": challenge}))

    info = probe_with_token(server)

    assert info.status == 403
    assert info.error == "insufficient_scope"
    assert info.required_scope == "api.write"


def test_other_403s_are_not_mistaken_for_an_auth_problem():
    server = FakeServer(httpx2.Response(403, text="blocked by a firewall"))

    assert probe_with_token(server) is None


def test_a_403_without_a_token_is_not_an_auth_explanation():
    challenge = 'Bearer error="insufficient_scope", scope="api.write"'
    server = FakeServer(httpx2.Response(403, headers={"WWW-Authenticate": challenge}))

    assert probe(server) is None  # no token was sent


def test_a_token_that_works_gives_nothing():
    assert probe_with_token(FakeServer(httpx2.Response(200))) is None


def test_quoted_challenge_values_are_unescaped():
    params = _challenge_params(r'Bearer error_description="the \"x\" token", error="e"')

    assert params["error_description"] == 'the "x" token'
    assert params["error"] == "e"

