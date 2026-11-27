import asyncio
import json
import logging
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult

from mcph.config import HttpServerConfig, OAuthAuthConfig
from mcph.oauth_login import (
    LoginError,
    _AnnouncingOAuth,
    _caused_by,
    _failure_message,
    _quiet_logging,
    login,
)
from mcph.token_store import TokenStore


def test_only_servers_that_use_oauth_can_be_logged_in_to(tmp_path):
    server = HttpServerConfig(name="docs", transport="http", url="http://x/mcp")

    with pytest.raises(LoginError, match="does not use `auth: {type: oauth}`"):
        asyncio.run(login(server, TokenStore(tmp_path)))


# --- failure messages -------------------------------------------------------------------------


def test_a_timeout_anywhere_in_the_chain_gives_the_timeout_message():
    try:
        try:
            raise TimeoutError("OAuth callback timed out after 2 seconds")
        except TimeoutError as timeout:
            raise RuntimeError("Client failed to connect: callback timed out") from timeout
    except RuntimeError as exc:
        wrapped = exc

    assert _failure_message(wrapped, 2) == (
        "timed out after 2 s waiting for the sign-in to finish"
    )


def test_a_timeout_inside_an_exception_group_is_found_too():
    group = ExceptionGroup("unhandled errors", [ExceptionGroup("inner", [TimeoutError()])])

    assert _caused_by(group, TimeoutError)
    assert "timed out after 7 s" in _failure_message(group, 7)


def test_other_errors_show_their_own_message_without_the_wrapper_prefix():
    exc = RuntimeError("Client failed to connect: All connection attempts failed")

    assert _failure_message(exc, 300) == "All connection attempts failed"


def test_the_first_real_error_in_a_group_is_shown():
    group = ExceptionGroup("unhandled errors", [ValueError("the redirect URI is not registered")])

    assert _failure_message(group, 300) == "the redirect URI is not registered"


def test_an_error_without_a_message_shows_its_type():
    assert _failure_message(KeyError(), 300) == "KeyError"


def test_exception_cycles_do_not_hang_the_search():
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__cause__, second.__cause__ = second, first

    assert not _caused_by(first, TimeoutError)


# --- logging --------------------------------------------------------------------------------


def levels():
    return logging.getLogger("fastmcp").level, logging.getLogger("mcp.client.auth").level


def test_library_logging_is_quiet_only_while_logging_in():
    before = levels()

    with _quiet_logging():
        assert levels() == (logging.WARNING, logging.CRITICAL)

    assert levels() == before


def test_library_logging_is_restored_after_an_error():
    before = levels()

    with pytest.raises(RuntimeError):
        with _quiet_logging():
            raise RuntimeError("boom")

    assert levels() == before


# --- an issuer announced with a trailing slash ------------------------------------------------

RESOURCE = "https://rs.test/mcp"


def server(**auth) -> HttpServerConfig:
    return HttpServerConfig(
        name="docs",
        transport="http",
        url=RESOURCE,
        auth=OAuthAuthConfig(type="oauth", **auth),
    )


def fake_servers(announced: str, issuer: str):
    """A resource server that announces ``announced`` as its authorization server,
    whose metadata says ``issuer``; everything else is as a real flow needs it."""
    host = f"{urlsplit(announced).scheme}://{urlsplit(announced).netloc}"

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == RESOURCE:
            if request.headers.get("authorization") == "Bearer tok":
                return httpx2.Response(200, json={"ok": True})
            return httpx2.Response(
                401,
                headers={
                    "WWW-Authenticate": "Bearer resource_metadata="
                    '"https://rs.test/.well-known/oauth-protected-resource/mcp"'
                },
            )
        if url == "https://rs.test/.well-known/oauth-protected-resource/mcp":
            return httpx2.Response(
                200, json={"resource": RESOURCE, "authorization_servers": [announced]}
            )
        if url.startswith(f"{host}/.well-known/oauth-authorization-server"):
            return httpx2.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": f"{host}/authorize",
                    "token_endpoint": f"{host}/token",
                    "registration_endpoint": f"{host}/register",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if url == f"{host}/register":
            return httpx2.Response(201, json={**json.loads(request.content), "client_id": "cid"})
        if url == f"{host}/token":
            return httpx2.Response(
                200, json={"access_token": "tok", "token_type": "Bearer", "expires_in": 900}
            )
        return httpx2.Response(404)

    return handler


class UnattendedOAuth(_AnnouncingOAuth):
    """The real flow, with the browser and the loopback redirect replaced by a user
    who approves at once."""

    state = ""

    async def redirect_handler(self, authorization_url: str) -> None:
        self.state = parse_qs(urlsplit(authorization_url).query)["state"][0]

    async def callback_handler(self) -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code="code123", state=self.state)


def sign_in(tmp_path, announced, issuer, **flags) -> httpx2.Response:
    target = server()
    oauth = UnattendedOAuth(
        mcp_url=RESOURCE,
        token_storage=TokenStore(tmp_path).open(target),
        client_name="mcph",
        announce=lambda url: None,
        **flags,
    )

    async def run() -> httpx2.Response:
        transport = httpx2.MockTransport(fake_servers(announced, issuer))
        async with httpx2.AsyncClient(auth=oauth, transport=transport) as http:
            return await http.post(RESOURCE, json={})

    return asyncio.run(run())


SLASH_ONLY = ("https://as.test/", "https://as.test")


@pytest.mark.parametrize("flags", [{}, {"ignore_issuer_trailing_slash": False}])
def test_an_issuer_that_differs_by_a_trailing_slash_is_refused_by_default(tmp_path, flags):
    with pytest.raises(OAuthFlowError, match="issuer mismatch"):
        sign_in(tmp_path, *SLASH_ONLY, **flags)


def test_the_trailing_slash_difference_can_be_accepted_on_request(tmp_path):
    response = sign_in(tmp_path, *SLASH_ONLY, ignore_issuer_trailing_slash=True)

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("announced", "issuer"),
    [
        ("https://as.test/", "https://as.test/"),  # consistent: the option does no harm
        ("https://as.test", "https://as.test"),
        ("https://as.test", "https://as.test/"),  # the slash is on the metadata side
    ],
)
def test_the_option_works_whichever_side_has_the_slash(tmp_path, announced, issuer):
    response = sign_in(tmp_path, announced, issuer, ignore_issuer_trailing_slash=True)

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("announced", "issuer"),
    [
        ("https://as.test/", "https://evil.test"),  # another server
        ("https://as.test/", "http://as.test"),  # another scheme
        ("https://as.test/realms/x/", "https://as.test/realms/x"),  # not a root issuer
        ("https://as.test/", "https://as.test/other"),  # another path
    ],
)
def test_only_a_root_issuers_trailing_slash_is_tolerated(tmp_path, announced, issuer):
    with pytest.raises(OAuthFlowError, match="issuer mismatch"):
        sign_in(tmp_path, announced, issuer, ignore_issuer_trailing_slash=True)


def expected_issuer(tmp_path, announced: str, ignore: bool) -> str:
    target = server()
    oauth = UnattendedOAuth(
        mcp_url=RESOURCE,
        token_storage=TokenStore(tmp_path).open(target),
        client_name="mcph",
        announce=lambda url: None,
        ignore_issuer_trailing_slash=ignore,
    )
    oauth.context.auth_server_url = announced
    return oauth._expected_issuer()


@pytest.mark.parametrize(
    ("announced", "other"),
    [
        ("https://as.test/", "https://as.test"),  # announced with a slash
        ("https://as.test", "https://as.test/"),  # and the other way round
        ("http://localhost:8080/", "http://localhost:8080"),
    ],
)
def test_with_the_option_both_spellings_of_a_root_issuer_are_the_same(
    tmp_path, announced, other
):
    expected = expected_issuer(tmp_path, announced, ignore=True)

    assert expected == other and not expected != other
    assert other == expected and not other != expected  # whichever side is on the left
    assert expected == announced
    assert str(expected) == announced  # what is shown or sent is unchanged


@pytest.mark.parametrize(
    ("announced", "other"),
    [
        ("https://as.test/", "https://evil.test"),
        ("https://as.test/", "http://as.test"),
        ("https://as.test/", "https://as.test/other"),
        ("https://as.test/realms/x/", "https://as.test/realms/x"),  # not a root issuer
        ("https://as.test:8443/", "https://as.test"),  # another port
    ],
)
def test_with_the_option_everything_else_still_differs(tmp_path, announced, other):
    expected = expected_issuer(tmp_path, announced, ignore=True)

    assert expected != other and not expected == other
    assert other != expected


def test_without_the_option_the_issuer_is_compared_strictly(tmp_path):
    expected = expected_issuer(tmp_path, "https://as.test/", ignore=False)

    assert expected == "https://as.test/"
    assert expected != "https://as.test"
    assert type(expected) is str


def sign_in_with_iss(tmp_path, iss: str | None) -> httpx2.Response:
    """Like ``sign_in`` with the option on, where the sign-in redirect carries ``iss``."""

    class WithIss(UnattendedOAuth):
        async def callback_handler(self) -> AuthorizationCodeResult:
            return AuthorizationCodeResult(code="code123", state=self.state, iss=iss)

    target = server()
    oauth = WithIss(
        mcp_url=RESOURCE,
        token_storage=TokenStore(tmp_path).open(target),
        client_name="mcph",
        announce=lambda url: None,
        ignore_issuer_trailing_slash=True,
    )

    async def run() -> httpx2.Response:
        transport = httpx2.MockTransport(fake_servers(*SLASH_ONLY))
        async with httpx2.AsyncClient(auth=oauth, transport=transport) as http:
            return await http.post(RESOURCE, json={})

    return asyncio.run(run())


@pytest.mark.parametrize("iss", ["https://as.test", None])
def test_a_sign_in_redirect_that_agrees_with_the_servers_metadata_is_accepted(
    tmp_path, iss
):
    assert sign_in_with_iss(tmp_path, iss).status_code == 200


@pytest.mark.parametrize("iss", ["https://evil.test", "https://as.test/"])
def test_a_sign_in_redirect_that_disagrees_with_the_servers_metadata_is_still_refused(
    tmp_path, iss
):
    # The option is about the issuer a server *announces*; the authorization
    # server itself must still be consistent with its own metadata.
    with pytest.raises(OAuthFlowError, match="iss mismatch"):
        sign_in_with_iss(tmp_path, iss)


# --- the hint on an issuer mismatch --------------------------------------------------------------

MISMATCH = OAuthFlowError(
    "Authorization server metadata issuer mismatch: https://a.test != https://a.test/"
)


def test_an_issuer_mismatch_points_at_the_setting():
    message = _failure_message(MISMATCH, 300)

    assert message.startswith("Authorization server metadata issuer mismatch: ")
    assert "set `ignore_issuer_trailing_slash: true` under `auth`" in message


def test_no_hint_when_the_setting_is_already_on():
    message = _failure_message(MISMATCH, 300, ignoring_slash=True)

    assert message == str(MISMATCH)


def test_other_errors_get_no_hint():
    message = _failure_message(RuntimeError("Client failed to connect: refused"), 300)

    assert message == "refused"

